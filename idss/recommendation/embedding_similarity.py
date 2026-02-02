"""
Embedding Similarity: SQL + Dense Vector Search + MMR Diversification

Ranks candidate vehicles using:
1. Dense embedding similarity to user preferences
2. MMR diversification for variety

Returns a ranked list that can be further bucketed by entropy-based diversification.
"""
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from idss.utils.logger import get_logger
from idss.recommendation.dense_ranker import rank_vehicles_by_dense_similarity
from idss.diversification.mmr import diversify_with_mmr

logger = get_logger("recommendation.embedding_similarity")


def rank_with_embedding_similarity(
    vehicles: List[Dict[str, Any]],
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any],
    top_k: int = 100,
    lambda_param: float = 0.85,
    use_mmr: bool = True
) -> List[Dict[str, Any]]:
    """
    Rank vehicles using Embedding Similarity: Dense Vector + MMR.

    Args:
        vehicles: Candidate vehicles from SQL query
        explicit_filters: User's explicit filters
        implicit_preferences: User's implicit preferences (liked_features, etc.)
        top_k: Number of top vehicles to return
        lambda_param: MMR diversity parameter (0=diverse, 1=relevant)
        use_mmr: Whether to apply MMR diversification

    Returns:
        List of ranked vehicles with _dense_score attached
    """
    if not vehicles:
        logger.warning("No vehicles to rank")
        return []

    logger.info(f"Embedding Similarity: Ranking {len(vehicles)} vehicles")
    logger.info(f"  Filters: {explicit_filters}")
    logger.info(f"  Preferences: {implicit_preferences}")

    # Step 1: Rank by dense embedding similarity
    try:
        ranked = rank_vehicles_by_dense_similarity(
            vehicles=vehicles,
            explicit_filters=explicit_filters,
            implicit_preferences=implicit_preferences,
            embedding_method="sum"
        )
    except Exception as e:
        logger.error(f"Dense ranking failed: {e}")
        # Fallback: return vehicles as-is
        return vehicles[:top_k]

    if not ranked:
        logger.warning("Dense ranking returned no results")
        return vehicles[:top_k]

    # Step 2: Apply MMR diversification if enabled
    if use_mmr and len(ranked) > top_k:
        # Convert to (score, vehicle) tuples for MMR
        scored_vehicles = [
            (v.get("_dense_score", 0.0), v) for v in ranked
        ]

        ranked = diversify_with_mmr(
            scored_vehicles=scored_vehicles,
            top_k=top_k,
            lambda_param=lambda_param
        )
    else:
        ranked = ranked[:top_k]

    logger.info(f"Embedding Similarity: Returning {len(ranked)} ranked vehicles")

    return ranked
