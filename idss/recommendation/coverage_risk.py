"""
Coverage-Risk Optimization for vehicle ranking.

Ranks candidate vehicles using:
1. Phrase-level semantic alignment (pros/cons matching)
2. Greedy selection maximizing coverage while minimizing risk
3. Optional soft constraints from relaxed filters

Returns a ranked list that can be further bucketed by entropy-based diversification.
"""
from pathlib import Path
from typing import Dict, Any, List, Optional

from idss.recommendation.phrase_store import PhraseStore
from idss.recommendation.preference_alignment import rank_vehicles_by_alignment
from idss.utils.logger import get_logger

logger = get_logger("recommendation.coverage_risk")

# Module-level cache for PhraseStore to avoid reloading model/embeddings
_PHRASE_STORE_CACHE: Optional[PhraseStore] = None


def get_phrase_store(
    reviews_db_path: Optional[Path] = None,
    vehicles_db_path: Optional[Path] = None,
    embeddings_dir: Optional[Path] = None,
    model_name: str = "all-mpnet-base-v2"
) -> PhraseStore:
    """
    Get cached PhraseStore instance to avoid reloading model/embeddings.

    This caches the phrase store globally so the sentence transformer model
    and phrase embeddings are only loaded once.

    Args:
        reviews_db_path: Path to Tavily reviews database
        vehicles_db_path: Path to unified vehicle listings database
        embeddings_dir: Path to pre-computed phrase embeddings
        model_name: Embedding model name

    Returns:
        Cached PhraseStore instance
    """
    global _PHRASE_STORE_CACHE

    if _PHRASE_STORE_CACHE is None:
        logger.info("Creating new PhraseStore (cache miss)")
        _PHRASE_STORE_CACHE = PhraseStore(
            reviews_db_path=reviews_db_path,
            vehicles_db_path=vehicles_db_path,
            embeddings_dir=embeddings_dir,
            model_name=model_name,
            preload=True
        )
    else:
        logger.debug("Using cached PhraseStore (cache hit)")

    return _PHRASE_STORE_CACHE


def rank_with_coverage_risk(
    vehicles: List[Dict[str, Any]],
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any],
    top_k: int = 100,
    lambda_risk: float = 0.5,
    mode: str = "sum",
    tau: float = 0.5,
    alpha: float = 1.0,
    relaxation_state: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Rank vehicles using Coverage-Risk Optimization.

    Args:
        vehicles: Candidate vehicles from SQL query
        explicit_filters: User's explicit filters
        implicit_preferences: User's implicit preferences (liked_features, disliked_features)
        top_k: Number of top vehicles to return
        lambda_risk: Risk penalty weight (default 0.5)
        mode: Aggregation mode - "max" or "sum" (default "sum")
        tau: Phrase-level similarity threshold (default 0.5)
        alpha: g function steepness for coverage mapping (default 1.0)
        relaxation_state: Dict with relaxed filters info (for soft constraints)

    Returns:
        List of ranked vehicles with alignment scores attached
    """
    if not vehicles:
        logger.warning("No vehicles to rank")
        return []

    logger.info(f"Coverage-Risk: Ranking {len(vehicles)} vehicles")
    logger.info(f"  Filters: {explicit_filters}")
    logger.info(f"  Preferences: {implicit_preferences}")

    # Check if we have implicit preferences to use for ranking
    liked = implicit_preferences.get("liked_features", [])
    disliked = implicit_preferences.get("disliked_features", [])

    if not liked and not disliked:
        logger.warning("No implicit preferences - returning vehicles in original order")
        return vehicles[:top_k]

    # Get cached phrase store
    try:
        phrase_store = get_phrase_store()
    except Exception as e:
        logger.error(f"Failed to load phrase store: {e}")
        # Fallback: return vehicles as-is
        return vehicles[:top_k]

    # Rank using coverage-risk optimization
    try:
        ranked = rank_vehicles_by_alignment(
            vehicles=vehicles,
            phrase_store=phrase_store,
            implicit_preferences=implicit_preferences,
            k=top_k,
            lambda_risk=lambda_risk,
            mode=mode,
            tau=tau,
            alpha=alpha,
            relaxation_state=relaxation_state,
            explicit_filters=explicit_filters
        )
    except Exception as e:
        logger.error(f"Coverage-Risk ranking failed: {e}")
        import traceback
        traceback.print_exc()
        # Fallback: return vehicles as-is
        return vehicles[:top_k]

    logger.info(f"Coverage-Risk: Returning {len(ranked)} ranked vehicles")

    return ranked
