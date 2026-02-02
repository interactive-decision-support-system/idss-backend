"""
Dense Embedding Ranker: Ranks vehicles using semantic similarity.

Uses pre-trained sentence transformers to understand semantic meaning
and natural language queries.
"""
from pathlib import Path
from typing import Dict, Any, List, Optional

from idss.recommendation.dense_embedding_store import DenseEmbeddingStore
from idss.utils.logger import get_logger

logger = get_logger("recommendation.dense_ranker")

# Module-level cache for DenseEmbeddingStore
_DENSE_STORE_CACHE: Dict[str, DenseEmbeddingStore] = {}


def get_dense_embedding_store(
    index_dir: Optional[Path] = None,
    model_name: str = "all-mpnet-base-v2",
    version: str = "v1",
    index_type: str = "Flat"
) -> DenseEmbeddingStore:
    """Get cached DenseEmbeddingStore instance."""
    cache_key = f"{index_dir}:{model_name}:{version}:{index_type}"

    if cache_key not in _DENSE_STORE_CACHE:
        logger.info(f"Creating new DenseEmbeddingStore")
        _DENSE_STORE_CACHE[cache_key] = DenseEmbeddingStore(
            index_dir=index_dir,
            model_name=model_name,
            version=version,
            index_type=index_type
        )

    return _DENSE_STORE_CACHE[cache_key]


def rank_vehicles_by_dense_similarity(
    vehicles: List[Dict[str, Any]],
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any],
    db_path: Optional[Path] = None,
    top_k: Optional[int] = None,
    embedding_method: str = "sum"
) -> List[Dict[str, Any]]:
    """
    Rank vehicles using dense embedding similarity.

    Args:
        vehicles: List of candidate vehicles to rank
        explicit_filters: User's explicit filters
        implicit_preferences: User's implicit preferences (liked_features, etc.)
        db_path: Path to vehicle database
        top_k: Optional limit on number of results
        embedding_method: "sum" (default) or "concat"

    Returns:
        List of vehicles ranked by semantic similarity
    """
    if not vehicles:
        return vehicles

    logger.info(f"Ranking {len(vehicles)} vehicles using dense embeddings")

    try:
        store = get_dense_embedding_store()
    except Exception as e:
        logger.error(f"Failed to load dense embedding store: {e}")
        return vehicles

    # Build query based on method
    if embedding_method == "sum":
        query_features = extract_query_features(explicit_filters, implicit_preferences)
        logger.info(f"Query features ({len(query_features)}): {query_features[:5]}")
        query_input = query_features
    else:
        query_text = build_query_text(explicit_filters, implicit_preferences)
        logger.info(f"Query: {query_text[:150]}")
        query_input = query_text

    # Get VINs from vehicles
    vin_to_vehicle = {}
    for vehicle in vehicles:
        vin = vehicle.get("vehicle", {}).get("vin") or vehicle.get("vin")
        if vin:
            vin_to_vehicle[vin] = vehicle

    if not vin_to_vehicle:
        logger.warning("No VINs found in vehicles")
        return vehicles

    # Search using dense embeddings
    try:
        vins, scores = store.search_by_vins(
            list(vin_to_vehicle.keys()),
            query_input,
            k=None,
            method=embedding_method
        )
    except Exception as e:
        logger.error(f"Dense search failed: {e}")
        return vehicles

    # Build ranked list with scores
    ranked = []
    for vin, score in zip(vins, scores):
        vehicle = vin_to_vehicle[vin]
        vehicle["_dense_score"] = float(score)  # Convert numpy float to Python float for JSON serialization
        ranked.append(vehicle)

    if top_k is not None:
        ranked = ranked[:top_k]

    if ranked:
        top_score = ranked[0].get("_dense_score", 0.0)
        logger.info(f"Ranked {len(ranked)} vehicles (top score: {top_score:.3f})")

    return ranked


def extract_query_features(
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any]
) -> List[str]:
    """Extract individual query features for sum-of-features embedding."""
    features = []

    # Vehicle Identity
    identity_parts = []
    if explicit_filters.get("make"):
        identity_parts.append(str(explicit_filters["make"]))
    if explicit_filters.get("model"):
        identity_parts.append(str(explicit_filters["model"]))
    if explicit_filters.get("trim"):
        identity_parts.append(str(explicit_filters["trim"]))
    if identity_parts:
        features.append(" ".join(identity_parts))

    # Body Style
    if explicit_filters.get("body_style"):
        features.append(f"{explicit_filters['body_style']} body style")

    # Fuel Type
    if explicit_filters.get("fuel_type"):
        features.append(f"{explicit_filters['fuel_type']} fuel")

    # Drivetrain
    if explicit_filters.get("drivetrain"):
        features.append(f"{explicit_filters['drivetrain']} drivetrain")

    # Transmission
    if explicit_filters.get("transmission"):
        features.append(f"{explicit_filters['transmission']} transmission")

    # Condition
    if explicit_filters.get("is_used") is False:
        features.append("new vehicle")
    elif explicit_filters.get("is_used") is True:
        features.append("used vehicle")

    # Implicit Preferences - liked_features
    liked_features = implicit_preferences.get("liked_features", []) or []
    for feature in liked_features:
        if feature:
            features.append(str(feature))

    # Notes
    if implicit_preferences.get("notes"):
        features.append(str(implicit_preferences["notes"]))

    return features


def build_query_text(
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any]
) -> str:
    """Build a structured query text from user preferences."""
    parts = []

    # Vehicle Identity
    identity_parts = []
    if explicit_filters.get("make"):
        identity_parts.append(str(explicit_filters["make"]))
    if explicit_filters.get("model"):
        identity_parts.append(str(explicit_filters["model"]))
    if identity_parts:
        parts.append(f"Vehicle: {' '.join(identity_parts)}")

    # Body Style
    if explicit_filters.get("body_style"):
        parts.append(f"Body Style: {explicit_filters['body_style']}")

    # Fuel Type
    if explicit_filters.get("fuel_type"):
        parts.append(f"Fuel: {explicit_filters['fuel_type']}")

    # Implicit Preferences
    liked_features = implicit_preferences.get("liked_features", []) or []
    if liked_features:
        parts.append(f"Features: {', '.join(str(f) for f in liked_features)}")

    query_text = ". ".join(parts) + "." if parts else "Vehicle"
    return query_text
