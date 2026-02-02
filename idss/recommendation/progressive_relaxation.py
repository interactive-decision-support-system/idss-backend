"""
Progressive Filter Relaxation.

Progressively relaxes filters from least to most important until results are found.
This prevents 0-result queries by gracefully loosening constraints.
"""
from typing import Any, Dict, List, Optional, Tuple

from idss.data.vehicle_store import LocalVehicleStore
from idss.utils.logger import get_logger

logger = get_logger("recommendation.progressive_relaxation")

# Filter importance ranking - filters earlier in list are LESS important (relaxed first)
# Filters later in list are MORE important (kept longest)
FILTER_RELAXATION_ORDER = [
    "search_radius",      # 1 - Willing to travel farther
    "interior_color",     # 2 - Cosmetic
    "exterior_color",     # 3 - Cosmetic
    "is_cpo",             # 4 - Certification is a plus
    "engine",             # 5 - Performance preference
    "trim",               # 6 - Specific variant
    "doors",              # 7 - Practical but flexible
    "year",               # 8 - Age preference (flexible)
    "mileage",            # 9 - Condition indicator (flexible)
    "price",              # 10 - Budget constraint
    "model",              # 11 - Specific model
    "make",               # 12 - Brand identity
    "drivetrain",         # 13 - Climate/terrain needs
    "seating_capacity",   # 14 - Family size
    "transmission",       # 15 - Manual vs automatic
    "fuel_type",          # 16 - Infrastructure/operating cost
    "is_used",            # 17 - New vs used
    "body_style",         # 18 - Fundamental vehicle type (MOST IMPORTANT)
]


def progressive_filter_relaxation(
    store: LocalVehicleStore,
    explicit_filters: Dict[str, Any],
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
    limit: int = 500,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Progressively relax filters from least to most important until we find ANY results.

    Key behavior:
    - If ANY vehicles match all criteria, return them (even if just 1)
    - Only relax filters when 0 results found
    - Track which filters were relaxed so we can inform the user

    Filter Relaxation Hierarchy (3 tiers):
    1. INFERRED filters are relaxed FIRST (least certain - derived from context)
    2. REGULAR filters are relaxed SECOND (explicit but flexible)
    3. MUST-HAVE filters are relaxed LAST (strict requirements)

    Within each tier, filters are relaxed according to FILTER_RELAXATION_ORDER.

    Args:
        store: LocalVehicleStore instance
        explicit_filters: All explicit filters from user
        user_latitude: User latitude for distance calculation
        user_longitude: User longitude for distance calculation
        limit: Maximum number of results to return

    Returns:
        Tuple of:
        - List of candidate vehicles
        - Relaxation state dict with:
            - all_criteria_met: True if no relaxation was needed
            - met_filters: List of filters that were satisfied
            - relaxed_filters: List of filters that were removed to find results
            - original_values: Dict of original values for relaxed filters
    """
    # Extract filter categories (if provided)
    must_have_filter_names = set(explicit_filters.get("must_have_filters", []))
    inferred_filter_names = set(explicit_filters.get("inferred_filters", []))
    avoid_vehicles = explicit_filters.get("avoid_vehicles")

    # Get all actual filter values (exclude metadata fields)
    metadata_fields = {"must_have_filters", "inferred_filters", "avoid_vehicles"}
    all_filters = {k: v for k, v in explicit_filters.items()
                   if k not in metadata_fields and v is not None}

    if not all_filters:
        logger.info("No filters to relax - querying with defaults")
        # Add default year range to avoid returning everything
        all_filters['year'] = '2018-2025'

    present_filters = set(all_filters.keys())

    # Build priority mapping with 3-tier hierarchy:
    # Tier 0: Inferred filters (priority 0-17) - relaxed FIRST
    # Tier 1: Regular filters (priority 18-35) - relaxed SECOND
    # Tier 2: Must-have filters (priority 36-53) - relaxed LAST
    TIER_SIZE = len(FILTER_RELAXATION_ORDER)

    filter_priorities = {}
    for filter_name in present_filters:
        # Get base priority from FILTER_RELAXATION_ORDER
        if filter_name in FILTER_RELAXATION_ORDER:
            base_priority = FILTER_RELAXATION_ORDER.index(filter_name)
        else:
            # Unranked filters get priority -1 (relaxed first within tier)
            base_priority = -1

        # Determine tier and calculate final priority
        if filter_name in inferred_filter_names:
            # Tier 0: Inferred filters (relaxed FIRST)
            tier_boost = 0
        elif filter_name in must_have_filter_names:
            # Tier 2: Must-have filters (relaxed LAST)
            tier_boost = 2 * TIER_SIZE
        else:
            # Tier 1: Regular filters (relaxed SECOND)
            tier_boost = 1 * TIER_SIZE

        filter_priorities[filter_name] = base_priority + tier_boost

    # Sort filters by priority (ascending - lower priority relaxed first)
    ranked_filters = sorted(present_filters, key=lambda f: filter_priorities[f])

    logger.info("=" * 60)
    logger.info("PROGRESSIVE FILTER RELAXATION")
    logger.info("=" * 60)
    logger.info(f"Starting filters: {list(all_filters.keys())}")
    logger.info(f"Relaxation order: {ranked_filters}")

    # Track relaxation state
    current_filters = all_filters.copy()
    relaxed_filters_list = []
    original_values = {}
    candidates = []

    # Try with all filters first
    iteration = 0
    while True:
        iteration += 1

        # Add avoid_vehicles back if present (never relax this)
        query_filters = current_filters.copy()
        if avoid_vehicles:
            query_filters["avoid_vehicles"] = avoid_vehicles

        logger.info(f"Iteration {iteration}: Testing with {len(current_filters)} filters: {list(current_filters.keys())}")

        candidates = store.search_listings(
            query_filters,
            limit=limit,
            order_by="price",
            order_dir="ASC",
            user_latitude=user_latitude,
            user_longitude=user_longitude
        )

        logger.info(f"  -> {len(candidates)} results")

        # Stop as soon as we find ANY results
        if len(candidates) > 0:
            logger.info(f"Found {len(candidates)} vehicles matching current criteria")
            break

        # Check if we've run out of filters to relax
        if not current_filters:
            logger.info("No more filters to relax. No vehicles found.")
            break

        # Find least important filter still present
        least_important = None
        for filter_name in ranked_filters:
            if filter_name in current_filters:
                least_important = filter_name
                break

        if least_important is None:
            logger.info("No more relaxable filters. No vehicles found.")
            break

        # Store the original value before removing
        original_values[least_important] = all_filters[least_important]
        relaxed_filters_list.append(least_important)

        # Remove the least important filter
        logger.info(f"  Relaxing filter: '{least_important}' (was: {all_filters[least_important]})")
        del current_filters[least_important]

    # Calculate relaxation state
    met_filters = list(current_filters.keys())
    all_criteria_met = len(relaxed_filters_list) == 0

    logger.info("=" * 60)
    logger.info(f"Final result: {len(candidates)} vehicles")
    logger.info(f"All criteria met: {all_criteria_met}")
    if relaxed_filters_list:
        logger.info(f"Relaxed filters: {relaxed_filters_list}")
    logger.info("=" * 60)

    # Build relaxation state
    relaxation_state = {
        "all_criteria_met": all_criteria_met,
        "met_filters": met_filters,
        "relaxed_filters": relaxed_filters_list,
        "original_values": original_values
    }

    return candidates, relaxation_state


__all__ = ["progressive_filter_relaxation", "FILTER_RELAXATION_ORDER"]
