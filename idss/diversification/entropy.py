"""
Entropy-based dimension selection for diversification.

Computes Shannon entropy for each dimension and selects the one
with highest entropy (most uncertainty) for diversification.
"""
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
import math

from idss.utils.logger import get_logger

logger = get_logger("diversification.entropy")

# Dimensions that can be diversified
# These are discovered dynamically from data, but this list defines
# which dimensions we consider for diversification
DIVERSIFIABLE_DIMENSIONS = [
    'price',
    'make',
    'body_style',
    'fuel_type',
    'drivetrain',
    'mileage',
    'year',
    'transmission',
]

# Numerical dimensions (need bucketing before entropy calculation)
NUMERICAL_DIMENSIONS = {'price', 'mileage', 'year'}


def get_vehicle_value(vehicle: Dict[str, Any], dimension: str) -> Any:
    """
    Extract a dimension value from a vehicle payload.

    Args:
        vehicle: Vehicle dictionary (Auto.dev format)
        dimension: Dimension name

    Returns:
        The value for that dimension, or None if not found
    """
    # Try vehicle section first
    v = vehicle.get('vehicle', {})

    if dimension == 'price':
        return v.get('price') or vehicle.get('retailListing', {}).get('price')
    elif dimension == 'mileage':
        return v.get('mileage') or vehicle.get('retailListing', {}).get('miles')
    elif dimension == 'year':
        return v.get('year')
    elif dimension == 'make':
        return v.get('make')
    elif dimension == 'model':
        return v.get('model')
    elif dimension == 'body_style':
        return v.get('bodyStyle') or v.get('norm_body_type')
    elif dimension == 'fuel_type':
        return v.get('fuel') or v.get('norm_fuel_type')
    elif dimension == 'drivetrain':
        return v.get('drivetrain')
    elif dimension == 'transmission':
        return v.get('transmission')
    else:
        return v.get(dimension)


def bucket_numerical_values(
    values: List[float],
    n_buckets: int = 3
) -> Tuple[List[int], List[Tuple[float, float]]]:
    """
    Bucket numerical values into n_buckets using quantiles.

    Args:
        values: List of numerical values
        n_buckets: Number of buckets to create

    Returns:
        Tuple of:
        - List of bucket indices (0 to n_buckets-1) for each value
        - List of (min, max) tuples defining bucket boundaries
    """
    if not values:
        return [], []

    # Filter out None values
    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return [0] * len(values), [(0, 0)]

    # Sort for quantile calculation
    sorted_values = sorted(valid_values)
    n = len(sorted_values)

    # Compute quantile boundaries
    boundaries = []
    for i in range(1, n_buckets):
        idx = int(n * i / n_buckets)
        boundaries.append(sorted_values[min(idx, n - 1)])

    # Define bucket ranges
    bucket_ranges = []
    prev = sorted_values[0]
    for boundary in boundaries:
        bucket_ranges.append((prev, boundary))
        prev = boundary
    bucket_ranges.append((prev, sorted_values[-1]))

    # Assign each value to a bucket
    bucket_indices = []
    for v in values:
        if v is None:
            bucket_indices.append(0)  # Default bucket for None
            continue

        assigned = False
        for i, (low, high) in enumerate(bucket_ranges):
            if i == len(bucket_ranges) - 1:
                # Last bucket includes the upper bound
                if low <= v <= high:
                    bucket_indices.append(i)
                    assigned = True
                    break
            else:
                if low <= v < high:
                    bucket_indices.append(i)
                    assigned = True
                    break

        if not assigned:
            bucket_indices.append(len(bucket_ranges) - 1)

    return bucket_indices, bucket_ranges


def compute_shannon_entropy(values: List[Any]) -> float:
    """
    Compute Shannon entropy for a list of values.

    H = -Σ p_i * log2(p_i)

    Args:
        values: List of values (can be any hashable type)

    Returns:
        Shannon entropy (higher = more diverse/uncertain)
    """
    if not values:
        return 0.0

    # Filter None values
    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return 0.0

    # Count frequencies
    counts = Counter(valid_values)
    total = len(valid_values)

    # Compute entropy
    entropy = 0.0
    for count in counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)

    return entropy


def compute_dimension_entropy(
    vehicles: List[Dict[str, Any]],
    dimension: str,
    n_buckets: int = 3
) -> float:
    """
    Compute entropy for a dimension across candidate vehicles.

    For numerical dimensions, values are bucketed first.

    Args:
        vehicles: List of vehicle dictionaries
        dimension: Dimension name
        n_buckets: Number of buckets for numerical dimensions

    Returns:
        Shannon entropy value (higher = more diverse/uncertain)
    """
    if not vehicles:
        return 0.0

    # Extract values
    values = [get_vehicle_value(v, dimension) for v in vehicles]

    # For numerical dimensions, bucket the values first
    if dimension in NUMERICAL_DIMENSIONS:
        numeric_values = []
        for v in values:
            if v is not None:
                try:
                    numeric_values.append(float(v))
                except (ValueError, TypeError):
                    numeric_values.append(None)
            else:
                numeric_values.append(None)

        bucket_indices, _ = bucket_numerical_values(numeric_values, n_buckets)
        values = bucket_indices

    return compute_shannon_entropy(values)


def discover_dimensions(vehicles: List[Dict[str, Any]]) -> List[str]:
    """
    Discover which dimensions have meaningful data in the vehicles.

    Args:
        vehicles: List of vehicle dictionaries

    Returns:
        List of dimension names that have data
    """
    available = []

    for dim in DIVERSIFIABLE_DIMENSIONS:
        # Check if at least 50% of vehicles have this dimension
        values = [get_vehicle_value(v, dim) for v in vehicles]
        non_null = [v for v in values if v is not None]

        if len(non_null) >= len(vehicles) * 0.5:
            available.append(dim)

    return available


def select_diversification_dimension(
    candidates: List[Dict[str, Any]],
    explicit_filters: Dict[str, Any],
    exclude_dimensions: Optional[List[str]] = None
) -> str:
    """
    Select the dimension to diversify based on entropy.

    Strategy:
    1. Identify dimensions NOT specified by user (unknown preferences)
    2. Compute entropy for each unspecified dimension
    3. Return dimension with highest entropy (most uncertainty)

    Args:
        candidates: Candidate vehicles after filtering
        explicit_filters: User's explicit filters (these are KNOWN)
        exclude_dimensions: Additional dimensions to exclude

    Returns:
        Dimension name to diversify along
    """
    if not candidates:
        return 'price'  # Default

    # Discover available dimensions
    available_dims = discover_dimensions(candidates)
    logger.info(f"Available dimensions: {available_dims}")

    # Exclude dimensions already specified by user
    specified = set(explicit_filters.keys())
    exclude = set(exclude_dimensions or [])

    unspecified_dims = [
        d for d in available_dims
        if d not in specified and d not in exclude
    ]

    logger.info(f"User specified: {specified}")
    logger.info(f"Unspecified dimensions for diversification: {unspecified_dims}")

    if not unspecified_dims:
        # All dimensions specified - default to price (always available)
        logger.info("All dimensions specified, defaulting to 'price'")
        return 'price'

    # Compute entropy for each unspecified dimension
    entropies = {}
    for dim in unspecified_dims:
        entropies[dim] = compute_dimension_entropy(candidates, dim)

    logger.info(f"Entropy scores: {entropies}")

    # Return dimension with highest entropy
    best_dim = max(entropies, key=entropies.get)
    logger.info(f"Selected diversification dimension: {best_dim} (entropy={entropies[best_dim]:.3f})")

    return best_dim


def compute_entropy_report(
    candidates: List[Dict[str, Any]],
    dimensions: Optional[List[str]] = None
) -> Dict[str, float]:
    """
    Compute entropy for all specified dimensions (for logging/analysis).

    Args:
        candidates: Candidate vehicles
        dimensions: List of dimensions to analyze (None = all available)

    Returns:
        Dict mapping dimension name to entropy value
    """
    if dimensions is None:
        dimensions = discover_dimensions(candidates)

    return {
        dim: compute_dimension_entropy(candidates, dim)
        for dim in dimensions
    }
