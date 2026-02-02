"""
Entropy-based question dimension selector.

Selects which dimension to ask about based on entropy analysis of the
current candidate set. Higher entropy = more uncertainty = more valuable
to ask about.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

from idss.diversification.entropy import (
    compute_shannon_entropy,
    bucket_numerical_values,
    DIVERSIFIABLE_DIMENSIONS,
    NUMERICAL_DIMENSIONS,
)
from idss.utils.logger import get_logger

logger = get_logger("interview.entropy_question_selector")

# Dimensions we can ask questions about
# Maps database field names to question-friendly topic names
QUESTIONABLE_DIMENSIONS = {
    "price": "budget",
    "body_style": "body type",
    "fuel_type": "fuel type",
    "drivetrain": "drivetrain",
    "make": "brand preference",
    "year": "vehicle age",
    "mileage": "mileage preference",
    "transmission": "transmission",
    "is_used": "new vs used",
}

# Map from explicit filter keys to dimension names
# Used to check if a dimension has been specified by the user
FILTER_TO_DIMENSION = {
    "price": "price",
    "body_style": "body_style",
    "fuel_type": "fuel_type",
    "make": "make",
    "model": "make",  # Model implies make is somewhat specified
    "year": "year",
    "mileage": "mileage",
    "drivetrain": "drivetrain",
    "transmission": "transmission",
    "is_used": "is_used",
}

# Dimension extraction from vehicle dict
# Handles both nested structure (vehicle/retailListing) and flat structure
def _get_nested(v, *keys):
    """Get value from nested vehicle structure or flat structure."""
    # Try flat structure first
    for key in keys:
        if v.get(key) is not None:
            return v.get(key)
    # Try nested structure
    vehicle = v.get("vehicle", {})
    retail = v.get("retailListing", {})
    for key in keys:
        if vehicle.get(key) is not None:
            return vehicle.get(key)
        if retail.get(key) is not None:
            return retail.get(key)
    return None

DIMENSION_EXTRACTORS = {
    "price": lambda v: _get_nested(v, "price"),
    "body_style": lambda v: _get_nested(v, "norm_body_type", "body_style"),
    "fuel_type": lambda v: _get_nested(v, "norm_fuel_type", "fuel_type"),
    "drivetrain": lambda v: _get_nested(v, "drivetrain"),
    "make": lambda v: _get_nested(v, "make"),
    "year": lambda v: _get_nested(v, "year"),
    "mileage": lambda v: _get_nested(v, "mileage", "miles"),
    "transmission": lambda v: _get_nested(v, "transmission"),
    "is_used": lambda v: "used" if _get_nested(v, "norm_is_used", "is_used") else "new",
}


def compute_dimension_entropy(
    candidates: List[Dict[str, Any]],
    dimension: str,
    n_buckets: int = 5,
) -> float:
    """
    Compute Shannon entropy for a specific dimension across candidates.

    Args:
        candidates: List of vehicle dictionaries
        dimension: Dimension to analyze
        n_buckets: Number of buckets for numerical dimensions

    Returns:
        Shannon entropy value (higher = more uncertainty)
    """
    if dimension not in DIMENSION_EXTRACTORS:
        return 0.0

    extractor = DIMENSION_EXTRACTORS[dimension]
    values = [extractor(v) for v in candidates]
    values = [v for v in values if v is not None]

    if not values:
        return 0.0

    # For numerical dimensions, bucket into string labels
    if dimension in NUMERICAL_DIMENSIONS:
        try:
            numeric_values = [float(v) for v in values if v is not None]
            if len(numeric_values) < 2:
                return 0.0

            # Create bucket labels instead of using bucket_numerical_values
            # which returns lists that aren't hashable
            min_val, max_val = min(numeric_values), max(numeric_values)
            bucket_size = (max_val - min_val) / n_buckets if max_val > min_val else 1

            def get_bucket_label(val):
                if bucket_size == 0:
                    return "all_same"
                bucket_idx = min(int((val - min_val) / bucket_size), n_buckets - 1)
                return f"bucket_{bucket_idx}"

            values = [get_bucket_label(v) for v in numeric_values]
        except (ValueError, TypeError):
            return 0.0

    # Convert any unhashable types to strings
    values = [str(v) if not isinstance(v, (str, int, float, bool)) else v for v in values]

    return compute_shannon_entropy(values)


def get_specified_dimensions(explicit_filters: Dict[str, Any]) -> Set[str]:
    """
    Get dimensions that have already been specified by user filters.

    Args:
        explicit_filters: Current explicit filters from session state

    Returns:
        Set of dimension names that are already specified
    """
    specified = set()
    for filter_key, value in explicit_filters.items():
        if value is not None and filter_key in FILTER_TO_DIMENSION:
            specified.add(FILTER_TO_DIMENSION[filter_key])
    return specified


def select_question_dimension(
    candidates: List[Dict[str, Any]],
    explicit_filters: Dict[str, Any],
    asked_dimensions: Set[str],
    min_entropy_threshold: float = 0.5,
) -> Optional[str]:
    """
    Select the dimension with highest entropy that hasn't been asked or specified.

    Args:
        candidates: Current candidate vehicles
        explicit_filters: User's explicit filters
        asked_dimensions: Dimensions already asked about
        min_entropy_threshold: Minimum entropy to consider a dimension worth asking

    Returns:
        Dimension name to ask about, or None if all covered
    """
    if not candidates:
        logger.warning("No candidates for entropy calculation")
        return None

    # Get dimensions already covered
    specified = get_specified_dimensions(explicit_filters)
    covered = specified | asked_dimensions

    logger.info(f"Entropy question selection:")
    logger.info(f"  Specified dimensions: {specified}")
    logger.info(f"  Asked dimensions: {asked_dimensions}")
    logger.info(f"  Total covered: {covered}")

    # Calculate entropy for each questionable dimension
    entropy_scores: List[Tuple[str, float]] = []

    for dimension in QUESTIONABLE_DIMENSIONS:
        if dimension in covered:
            continue

        entropy = compute_dimension_entropy(candidates, dimension)
        entropy_scores.append((dimension, entropy))
        logger.debug(f"  {dimension}: entropy={entropy:.3f}")

    if not entropy_scores:
        logger.info("All dimensions covered - no question to ask")
        return None

    # Sort by entropy descending
    entropy_scores.sort(key=lambda x: x[1], reverse=True)

    # Log entropy ranking
    logger.info("Entropy ranking (uncovered dimensions):")
    for dim, ent in entropy_scores[:5]:
        topic = QUESTIONABLE_DIMENSIONS.get(dim, dim)
        logger.info(f"  {dim} ({topic}): {ent:.3f}")

    # Select highest entropy dimension above threshold
    best_dim, best_entropy = entropy_scores[0]

    if best_entropy < min_entropy_threshold:
        logger.info(f"Best entropy {best_entropy:.3f} below threshold {min_entropy_threshold}")
        return None

    logger.info(f"Selected dimension: {best_dim} (entropy={best_entropy:.3f})")
    return best_dim


def get_dimension_topic(dimension: str) -> str:
    """Get human-readable topic name for a dimension."""
    return QUESTIONABLE_DIMENSIONS.get(dimension, dimension)


def get_dimension_context(
    dimension: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Get context about a dimension's distribution for LLM question generation.

    Args:
        dimension: Dimension to analyze
        candidates: Current candidates

    Returns:
        Dict with distribution info for the LLM prompt
    """
    if dimension not in DIMENSION_EXTRACTORS:
        return {"dimension": dimension, "topic": get_dimension_topic(dimension)}

    extractor = DIMENSION_EXTRACTORS[dimension]
    values = [extractor(v) for v in candidates]
    values = [v for v in values if v is not None]

    context = {
        "dimension": dimension,
        "topic": get_dimension_topic(dimension),
        "total_candidates": len(candidates),
        "values_present": len(values),
    }

    if not values:
        return context

    if dimension in NUMERICAL_DIMENSIONS:
        try:
            numeric_values = [float(v) for v in values]
            context["min"] = min(numeric_values)
            context["max"] = max(numeric_values)
            context["is_numerical"] = True

            # Format for display
            if dimension == "price":
                context["range_display"] = f"${int(context['min']):,} - ${int(context['max']):,}"
            elif dimension == "mileage":
                context["range_display"] = f"{int(context['min']):,} - {int(context['max']):,} miles"
            elif dimension == "year":
                context["range_display"] = f"{int(context['min'])} - {int(context['max'])}"
        except (ValueError, TypeError):
            pass
    else:
        # Categorical - get top values
        from collections import Counter
        counts = Counter(values)
        top_values = counts.most_common(5)
        context["top_values"] = [v for v, c in top_values]
        context["is_categorical"] = True

    return context


__all__ = [
    "select_question_dimension",
    "get_dimension_topic",
    "get_dimension_context",
    "get_specified_dimensions",
    "QUESTIONABLE_DIMENSIONS",
]
