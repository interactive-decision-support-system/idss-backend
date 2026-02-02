"""
Data-driven bucketing for vehicle diversification.

Creates buckets based on data distribution using quantiles,
without hardcoded boundary values.
"""
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from idss.utils.logger import get_logger
from idss.diversification.entropy import get_vehicle_value, NUMERICAL_DIMENSIONS

logger = get_logger("diversification.bucketing")


def generate_label(dimension: str, low: float, high: float) -> str:
    """
    Generate human-readable label for a bucket range.

    Args:
        dimension: Dimension name
        low: Lower bound
        high: Upper bound

    Returns:
        Human-readable label string
    """
    if dimension == 'price':
        if high >= 1_000_000:
            return f"${low/1000:.0f}K+"
        return f"${low/1000:.0f}K - ${high/1000:.0f}K"
    elif dimension == 'mileage':
        if high >= 500_000:
            return f"{low/1000:.0f}K+ miles"
        return f"{low/1000:.0f}K - {high/1000:.0f}K miles"
    elif dimension == 'year':
        if low == high:
            return f"{int(low)}"
        return f"{int(low)} - {int(high)}"
    else:
        return f"{low:.1f} - {high:.1f}"


def compute_quantile_boundaries(
    values: List[float],
    n_buckets: int = 3
) -> List[float]:
    """
    Compute quantile boundaries for equal-count bucketing.

    Args:
        values: List of numerical values (None values filtered out)
        n_buckets: Number of buckets

    Returns:
        List of boundary values (n_buckets - 1 boundaries)
    """
    if not values:
        return []

    # Use numpy percentile for accurate quantile calculation
    percentiles = [100 * i / n_buckets for i in range(1, n_buckets)]
    boundaries = np.percentile(values, percentiles).tolist()

    return boundaries


def bucket_vehicles_numerical(
    vehicles: List[Dict[str, Any]],
    dimension: str,
    n_buckets: int = 3,
    n_per_bucket: int = 3
) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Bucket vehicles by a numerical dimension using quantiles.

    Args:
        vehicles: List of vehicle dictionaries
        dimension: Numerical dimension to bucket by
        n_buckets: Number of buckets to create
        n_per_bucket: Maximum vehicles per bucket

    Returns:
        Tuple of:
        - List of buckets (each bucket is a list of vehicles)
        - List of bucket labels
    """
    if not vehicles:
        return [[] for _ in range(n_buckets)], ["No data"] * n_buckets

    # Extract values and track indices
    values_with_idx = []
    for i, v in enumerate(vehicles):
        val = get_vehicle_value(v, dimension)
        if val is not None:
            try:
                values_with_idx.append((float(val), i))
            except (ValueError, TypeError):
                pass

    if not values_with_idx:
        return [vehicles[:n_per_bucket]], [f"All ({dimension} unknown)"]

    # Sort by value
    values_with_idx.sort(key=lambda x: x[0])
    sorted_values = [v for v, _ in values_with_idx]

    # Compute quantile boundaries
    boundaries = compute_quantile_boundaries(sorted_values, n_buckets)

    # Create bucket ranges
    min_val = sorted_values[0]
    max_val = sorted_values[-1]

    bucket_ranges = []
    prev = min_val
    for boundary in boundaries:
        bucket_ranges.append((prev, boundary))
        prev = boundary
    bucket_ranges.append((prev, max_val))

    # Assign vehicles to buckets
    buckets: List[List[Dict[str, Any]]] = [[] for _ in range(n_buckets)]

    for val, idx in values_with_idx:
        for bucket_idx, (low, high) in enumerate(bucket_ranges):
            if bucket_idx == len(bucket_ranges) - 1:
                # Last bucket includes upper bound
                if low <= val <= high:
                    if len(buckets[bucket_idx]) < n_per_bucket:
                        buckets[bucket_idx].append(vehicles[idx])
                    break
            else:
                if low <= val < high:
                    if len(buckets[bucket_idx]) < n_per_bucket:
                        buckets[bucket_idx].append(vehicles[idx])
                    break

    # Generate labels
    labels = [
        generate_label(dimension, low, high)
        for low, high in bucket_ranges
    ]

    return buckets, labels


def bucket_vehicles_categorical(
    vehicles: List[Dict[str, Any]],
    dimension: str,
    n_buckets: int = 3,
    n_per_bucket: int = 3
) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Bucket vehicles by a categorical dimension.

    Takes the top n_buckets most common values.

    Args:
        vehicles: List of vehicle dictionaries
        dimension: Categorical dimension to bucket by
        n_buckets: Number of buckets to create
        n_per_bucket: Maximum vehicles per bucket

    Returns:
        Tuple of:
        - List of buckets (each bucket is a list of vehicles)
        - List of bucket labels (the category values)
    """
    if not vehicles:
        return [[] for _ in range(n_buckets)], ["No data"] * n_buckets

    # Group vehicles by value
    by_value: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in vehicles:
        val = get_vehicle_value(v, dimension)
        if val is not None:
            by_value[str(val)].append(v)

    if not by_value:
        return [vehicles[:n_per_bucket]], [f"All ({dimension} unknown)"]

    # Sort by count (most popular first)
    sorted_values = sorted(by_value.items(), key=lambda x: len(x[1]), reverse=True)

    # Take top n_buckets
    top_values = sorted_values[:n_buckets]

    # Build output
    buckets = []
    labels = []

    for val, bucket_vehicles in top_values:
        buckets.append(bucket_vehicles[:n_per_bucket])
        labels.append(str(val))

    # Pad with empty buckets if needed
    while len(buckets) < n_buckets:
        buckets.append([])
        labels.append("Other")

    return buckets, labels


def bucket_vehicles(
    vehicles: List[Dict[str, Any]],
    dimension: str,
    n_buckets: int = 3,
    n_per_bucket: int = 3
) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Bucket vehicles by a dimension (auto-detects numerical vs categorical).

    Args:
        vehicles: List of vehicle dictionaries
        dimension: Dimension to bucket by
        n_buckets: Number of buckets to create
        n_per_bucket: Maximum vehicles per bucket

    Returns:
        Tuple of:
        - List of buckets (each bucket is a list of vehicles)
        - List of bucket labels
    """
    if dimension in NUMERICAL_DIMENSIONS:
        return bucket_vehicles_numerical(vehicles, dimension, n_buckets, n_per_bucket)
    else:
        return bucket_vehicles_categorical(vehicles, dimension, n_buckets, n_per_bucket)


def diversify_with_entropy_bucketing(
    vehicles: List[Dict[str, Any]],
    dimension: str,
    n_rows: int = 3,
    n_per_row: int = 3
) -> Tuple[List[List[Dict[str, Any]]], List[str], str]:
    """
    Diversify vehicles using entropy-based bucketing.

    This is the main entry point for diversification.

    Args:
        vehicles: Ranked list of vehicles (from recommendation method)
        dimension: Dimension to diversify along
        n_rows: Number of output rows (buckets)
        n_per_row: Vehicles per row

    Returns:
        Tuple of:
        - 2D list of vehicles [n_rows][n_per_row]
        - List of row labels
        - The dimension used for diversification
    """
    logger.info(f"Diversifying {len(vehicles)} vehicles by {dimension}")
    logger.info(f"Output format: {n_rows} rows x {n_per_row} vehicles per row")

    buckets, labels = bucket_vehicles(
        vehicles,
        dimension,
        n_buckets=n_rows,
        n_per_bucket=n_per_row
    )

    # Log result
    total = sum(len(b) for b in buckets)
    logger.info(f"Diversification result: {total} vehicles in {len(buckets)} buckets")
    for i, (bucket, label) in enumerate(zip(buckets, labels)):
        logger.info(f"  Bucket {i+1} ({label}): {len(bucket)} vehicles")

    return buckets, labels, dimension
