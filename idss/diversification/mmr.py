"""
Diversification utilities for recommendation ranking.

Implements Maximal Marginal Relevance (MMR) for diverse top-k selection.
"""
from typing import Dict, Any, List, Tuple

from idss.utils.logger import get_logger

logger = get_logger("diversification.mmr")


def compute_vehicle_similarity(v1: Dict[str, Any], v2: Dict[str, Any]) -> float:
    """
    Compute similarity between two vehicles (0.0 = diverse, 1.0 = identical).

    Similarity is based on make, model, and body_style overlap.
    """
    vehicle1 = v1.get("vehicle", {})
    vehicle2 = v2.get("vehicle", {})

    make1 = str(vehicle1.get("make", "")).lower()
    make2 = str(vehicle2.get("make", "")).lower()
    model1 = str(vehicle1.get("model", "")).lower()
    model2 = str(vehicle2.get("model", "")).lower()
    body1 = str(vehicle1.get("bodyStyle", "") or v1.get("body_style", "")).lower()
    body2 = str(vehicle2.get("bodyStyle", "") or v2.get("body_style", "")).lower()

    # Same make and model = very similar
    if make1 == make2 and model1 == model2:
        return 0.9

    # Same make, different model = moderately similar
    if make1 == make2:
        if body1 == body2 and body1:
            return 0.7
        return 0.6

    # Different make, same body style = somewhat similar
    if body1 == body2 and body1:
        return 0.4

    # Completely different
    return 0.0


def diversify_with_mmr(
    scored_vehicles: List[Tuple[float, Dict[str, Any]]],
    top_k: int = 20,
    lambda_param: float = 0.7,
) -> List[Dict[str, Any]]:
    """
    Apply Maximal Marginal Relevance (MMR) to select diverse top-k vehicles.

    Args:
        scored_vehicles: List of (relevance_score, vehicle_dict) tuples
        top_k: Number of vehicles to select
        lambda_param: Trade-off between relevance and diversity (0.6-0.8 recommended)

    Returns:
        List of top_k vehicles selected via MMR
    """
    if len(scored_vehicles) <= top_k:
        return [vehicle for _, vehicle in scored_vehicles]

    selected: List[Tuple[float, Dict[str, Any]]] = [scored_vehicles[0]]
    remaining = list(scored_vehicles[1:])

    while len(selected) < top_k and remaining:
        best_mmr_score = -float('inf')
        best_idx = 0

        for idx, (relevance, candidate) in enumerate(remaining):
            max_similarity = max(
                compute_vehicle_similarity(candidate, selected_vehicle)
                for _, selected_vehicle in selected
            )
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity

            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_idx = idx

        selected.append(remaining.pop(best_idx))

    logger.info(f"MMR: selected {len(selected)} from {len(scored_vehicles)} (lambda={lambda_param})")
    return [vehicle for _, vehicle in selected]


def diversify_with_clustered_mmr(
    scored_vehicles: List[Tuple[float, Dict[str, Any]]],
    top_k: int = 20,
    cluster_size: int = 3,
    lambda_param: float = 0.7,
) -> List[Dict[str, Any]]:
    """
    Apply MMR in clusters to balance diversity and similarity.

    Creates clusters of similar vehicles, allowing users to compare similar options.
    """
    if len(scored_vehicles) <= top_k:
        return [vehicle for _, vehicle in scored_vehicles]

    selected: List[Tuple[float, Dict[str, Any]]] = []
    remaining = list(scored_vehicles)
    num_clusters = (top_k + cluster_size - 1) // cluster_size

    for cluster_idx in range(num_clusters):
        if not remaining:
            break

        vehicles_needed = min(cluster_size, top_k - len(selected))
        if vehicles_needed <= 0:
            break

        cluster = []
        cluster.append(remaining.pop(0))

        for _ in range(vehicles_needed - 1):
            if not remaining:
                break

            best_mmr_score = -float('inf')
            best_idx = 0

            for idx, (relevance, candidate) in enumerate(remaining):
                max_similarity = max(
                    compute_vehicle_similarity(candidate, cluster_vehicle)
                    for _, cluster_vehicle in cluster
                )
                mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity

                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_idx = idx

            cluster.append(remaining.pop(best_idx))

        selected.extend(cluster)

    logger.info(f"Clustered MMR: {len(selected)} vehicles in {num_clusters} clusters")
    return [vehicle for _, vehicle in selected]
