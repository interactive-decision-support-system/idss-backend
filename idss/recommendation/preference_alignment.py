"""
Preference Alignment Scoring for Method 3: Coverage-Risk Optimization.

Implements the semantic coverage-risk optimization objective from the formal specification:

Objective:
    max_{|S|=K} Coverage(S) - λ·Risk(S) + μ·SoftBonus(S)

Where:
    Coverage(S) = Σ_u [1 - Π_{v∈S} (1 - g(Pos(u,v)))]  (noisy-or, sum mode)
               or Σ_u max_{v∈S} Pos(u,v)               (max mode)

    Risk(S) = Σ_r Σ_{v∈S} h(Neg(r,v))                  (sum mode)
           or Σ_r max_{v∈S} Neg(r,v)                   (max mode)

    SoftBonus(S) = Σ_{v∈S} Σ_c η_c · Sat(c,v)

Alignment scores:
    Pos(u,v) = Σ_{k:pros} φ(cos(u, z_vk))  where φ(t) = max(0, t-τ)  (sum mode)
            or max_{k:pros} cos(u, z_vk)                              (max mode)

Two modes supported:
    - "max": Original implementation using max pooling (faster, simpler)
    - "sum": Full specification with sum aggregation and noisy-or coverage (submodular)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from idss.recommendation.phrase_store import PhraseStore, VehiclePhrases
from idss.utils.logger import get_logger

logger = get_logger("recommendation.preference_alignment")


class AggregationMode(Enum):
    """Aggregation mode for alignment scores."""
    MAX = "max"  # Original: max over phrases, max over vehicles
    SUM = "sum"  # Proposed: sum over phrases with threshold, noisy-or coverage


@dataclass
class SoftConstraint:
    """A soft comparable constraint from relaxed hard filters."""
    name: str  # e.g., "price", "year", "mileage"
    constraint_type: str  # "range", "categorical", "max", "min"
    original_value: Any  # Original constraint value
    weight: float = 1.0  # η_c weight for this constraint

    def satisfies(self, vehicle: Dict) -> bool:
        """Check if vehicle satisfies this soft constraint (Sat(c,v))."""
        vehicle_value = vehicle.get(self.name)
        if vehicle_value is None:
            return False

        if self.constraint_type == "range":
            # original_value is (min_val, max_val)
            min_val, max_val = self.original_value
            if min_val is not None and vehicle_value < min_val:
                return False
            if max_val is not None and vehicle_value > max_val:
                return False
            return True

        elif self.constraint_type == "max":
            return vehicle_value <= self.original_value

        elif self.constraint_type == "min":
            return vehicle_value >= self.original_value

        elif self.constraint_type == "categorical":
            # original_value is a set/list of acceptable values
            if isinstance(self.original_value, (list, set)):
                return vehicle_value in self.original_value
            return vehicle_value == self.original_value

        return False


def g_function(x: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    """
    Coverage mapping function: g(x) = 1 - exp(-α·x)

    Maps alignment scores to [0, 1] with diminishing returns.

    Args:
        x: Alignment scores (non-negative)
        alpha: Steepness parameter (default 1.0)

    Returns:
        Mapped scores in [0, 1]
    """
    return 1 - np.exp(-alpha * np.maximum(x, 0))


def h_function(x: np.ndarray) -> np.ndarray:
    """
    Risk mapping function: h(x) = x (linear)

    Args:
        x: Risk scores (non-negative)

    Returns:
        Mapped risk scores
    """
    return np.maximum(x, 0)


def phi_threshold(similarities: np.ndarray, tau: float) -> np.ndarray:
    """
    Threshold function: φ(t) = max(0, t - τ)

    Filters out weak matches below confidence threshold.

    Args:
        similarities: Cosine similarities
        tau: Confidence threshold

    Returns:
        Thresholded similarities (0 if below tau)
    """
    return np.maximum(0, similarities - tau)


def compute_alignment_scores(
    vehicle_phrases: VehiclePhrases,
    liked_embeddings: np.ndarray,
    disliked_embeddings: np.ndarray,
    mode: AggregationMode = AggregationMode.MAX,
    tau: float = 0.5
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute Pos and Neg alignment scores for a vehicle.

    Both modes apply φ(t) = max(0, t - τ) at the phrase level to filter weak matches.

    Args:
        vehicle_phrases: Pre-computed phrase embeddings for vehicle
        liked_embeddings: Embeddings for user's liked features (M, D)
        disliked_embeddings: Embeddings for user's disliked features (N, D)
        mode: Aggregation mode (MAX or SUM)
        tau: Similarity threshold φ(t) = max(0, t - τ) (default 0.5)

    Returns:
        Tuple of (pos_scores, neg_scores):
        - pos_scores: (M,) array of Pos_j(v) for each liked feature j
        - neg_scores: (N,) array of Neg_j(v) for each disliked feature j
    """
    M = liked_embeddings.shape[0] if len(liked_embeddings.shape) > 0 else 0
    N = disliked_embeddings.shape[0] if len(disliked_embeddings.shape) > 0 else 0

    # Handle empty cases
    if M == 0:
        pos_scores = np.array([])
    elif vehicle_phrases.pros_embeddings.shape[0] == 0:
        pos_scores = np.zeros(M)
    else:
        # Compute Pos_j(v) for each liked feature j
        # Shape: (M liked, K pros)
        pros_similarities = liked_embeddings @ vehicle_phrases.pros_embeddings.T

        # Apply φ(t) = max(0, t - τ) to filter weak matches at phrase level
        thresholded = phi_threshold(pros_similarities, tau)

        if mode == AggregationMode.MAX:
            # Max over thresholded phrases: Pos_j(v) = max_k φ(cos(u_j, z_vk))
            pos_scores = np.max(thresholded, axis=1)
        else:
            # Sum over thresholded phrases: Pos_j(v) = Σ_k φ(cos(u_j, z_vk))
            pos_scores = np.sum(thresholded, axis=1)

    if N == 0:
        neg_scores = np.array([])
    elif vehicle_phrases.cons_embeddings.shape[0] == 0:
        neg_scores = np.zeros(N)
    else:
        # Compute Neg_j(v) for each disliked feature j
        # Shape: (N disliked, K cons)
        cons_similarities = disliked_embeddings @ vehicle_phrases.cons_embeddings.T

        # Apply φ(t) = max(0, t - τ) to filter weak matches at phrase level
        thresholded = phi_threshold(cons_similarities, tau)

        if mode == AggregationMode.MAX:
            # Max over thresholded phrases: Neg_j(v) = max_k φ(cos(r_j, z_vk))
            neg_scores = np.max(thresholded, axis=1)
        else:
            # Sum over thresholded phrases: Neg_j(v) = Σ_k φ(cos(r_j, z_vk))
            neg_scores = np.sum(thresholded, axis=1)

    return pos_scores, neg_scores


def compute_alignment_matrix(
    vehicles: List[Dict],
    phrase_store: PhraseStore,
    implicit_preferences: Dict[str, Any],
    mode: AggregationMode = AggregationMode.MAX,
    tau: float = 0.5
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    """
    Compute Pos and Neg alignment matrices for a set of vehicles.

    Args:
        vehicles: List of vehicle dicts with make, model, year
        phrase_store: Pre-loaded phrase store
        implicit_preferences: User's implicit preferences
        mode: Aggregation mode (MAX or SUM)
        tau: Similarity threshold φ(t) = max(0, t - τ) (default 0.5)

    Returns:
        Tuple of (Pos, Neg, liked_features, disliked_features):
        - Pos: (V vehicles, M liked) matrix of Pos_j(v) scores
        - Neg: (V vehicles, N disliked) matrix of Neg_j(v) scores
        - liked_features: List of M liked feature strings
        - disliked_features: List of N disliked feature strings
    """
    # Extract preferences
    liked_features = implicit_preferences.get("liked_features", []) or []
    disliked_features = implicit_preferences.get("disliked_features", []) or []

    if not liked_features and not disliked_features:
        logger.warning("No implicit preferences provided - returning zero scores")
        return np.zeros((len(vehicles), 0)), np.zeros((len(vehicles), 0)), [], []

    # Encode user preferences
    liked_embeddings = phrase_store.encode_batch(liked_features) if liked_features else np.array([])
    disliked_embeddings = phrase_store.encode_batch(disliked_features) if disliked_features else np.array([])

    # Compute scores for each vehicle
    pos_matrix = []
    neg_matrix = []

    for vehicle in vehicles:
        # Get phrase embeddings for this vehicle (with type safety)
        make = vehicle.get("make")
        model = vehicle.get("model")
        year = vehicle.get("year")

        # Handle missing or invalid data
        if not make or not model or year is None:
            pos_scores = np.zeros(len(liked_features)) if liked_features else np.array([])
            neg_scores = np.zeros(len(disliked_features)) if disliked_features else np.array([])
            pos_matrix.append(pos_scores)
            neg_matrix.append(neg_scores)
            continue

        # Ensure make and model are strings (handles cases like Polestar 3 where model=3 as int)
        make = str(make) if not isinstance(make, str) else make
        model = str(model) if not isinstance(model, str) else model
        year = int(year) if not isinstance(year, int) else year

        vehicle_phrases = phrase_store.get_phrases(make, model, year)

        if vehicle_phrases is None:
            # No phrases available - use zeros
            pos_scores = np.zeros(len(liked_features)) if liked_features else np.array([])
            neg_scores = np.zeros(len(disliked_features)) if disliked_features else np.array([])
        else:
            # Compute alignment scores
            pos_scores, neg_scores = compute_alignment_scores(
                vehicle_phrases,
                liked_embeddings,
                disliked_embeddings,
                mode=mode,
                tau=tau
            )

        pos_matrix.append(pos_scores)
        neg_matrix.append(neg_scores)

    # Stack into matrices
    Pos = np.array(pos_matrix) if pos_matrix else np.zeros((len(vehicles), 0))
    Neg = np.array(neg_matrix) if neg_matrix else np.zeros((len(vehicles), 0))

    logger.info(f"Computed alignment matrix ({mode.value} mode): {Pos.shape[0]} vehicles × "
                f"({Pos.shape[1]} liked + {Neg.shape[1]} disliked) preferences")

    return Pos, Neg, liked_features, disliked_features


def compute_soft_bonus_vector(
    vehicles: List[Dict],
    soft_constraints: List[SoftConstraint]
) -> np.ndarray:
    """
    Compute soft constraint bonus for each vehicle.

    B(v) = Σ_c η_c · Sat(c, v)

    Args:
        vehicles: List of vehicle dicts
        soft_constraints: List of soft constraints from relaxed filters

    Returns:
        (V,) array of soft bonus scores
    """
    if not soft_constraints:
        return np.zeros(len(vehicles))

    bonus_scores = np.zeros(len(vehicles))

    for i, vehicle in enumerate(vehicles):
        for constraint in soft_constraints:
            if constraint.satisfies(vehicle):
                bonus_scores[i] += constraint.weight

    return bonus_scores


def calibrate_mu(
    Pos: np.ndarray,
    soft_bonus: np.ndarray,
    mode: AggregationMode = AggregationMode.MAX,
    alpha: float = 1.0,
    rho: float = 1.0,
    epsilon: float = 1e-6
) -> float:
    """
    Calibrate μ using scale matching rule.

    μ = ρ · median(Δ_F(v|∅)) / (median(Δ_B(v)) + ε)

    This ensures soft bonus contribution is comparable to coverage gains.

    Args:
        Pos: (V, M) alignment matrix
        soft_bonus: (V,) soft bonus scores
        mode: Aggregation mode
        alpha: g function steepness
        rho: Scaling constant (default 1.0)
        epsilon: Numerical stability term

    Returns:
        Calibrated μ value
    """
    if Pos.shape[1] == 0 or np.all(soft_bonus == 0):
        return 0.0

    # Compute singleton coverage marginals Δ_F(v|∅)
    if mode == AggregationMode.MAX:
        # For max mode: Δ_F(v|∅) = Σ_u Pos(u,v)
        coverage_marginals = np.sum(Pos, axis=1)
    else:
        # For sum mode: Δ_F(v|∅) = Σ_u g(Pos(u,v))
        coverage_marginals = np.sum(g_function(Pos, alpha), axis=1)

    # Compute medians
    median_coverage = np.median(coverage_marginals)
    median_bonus = np.median(soft_bonus[soft_bonus > 0]) if np.any(soft_bonus > 0) else 0.0

    # Scale matching
    mu = rho * median_coverage / (median_bonus + epsilon)

    logger.info(f"Calibrated μ = {mu:.4f} (median_cov={median_coverage:.4f}, median_bonus={median_bonus:.4f})")

    return mu


def greedy_select_vehicles(
    Pos: np.ndarray,
    Neg: np.ndarray,
    soft_bonus: np.ndarray,
    k: int = 20,
    lambda_risk: float = 0.5,
    mu: float = 0.0,
    mode: AggregationMode = AggregationMode.MAX,
    min_similarity: float = 0.5,
    alpha: float = 1.0
) -> List[int]:
    """
    Greedy algorithm for coverage-risk optimization (VECTORIZED for speed).

    Implements Algorithm 1 from the specification:
    - Iteratively select vehicle with highest marginal gain
    - Marginal gain = Coverage_gain - λ·Risk_gain + μ·Bonus_gain

    Time complexity: O(K * V * M) instead of O(K * V * K * M)

    Args:
        Pos: (V, M) matrix of Pos_j(v) scores
        Neg: (V, N) matrix of Neg_j(v) scores
        soft_bonus: (V,) array of soft bonus scores
        k: Number of vehicles to select
        lambda_risk: Risk penalty weight
        mu: Soft bonus weight
        mode: Aggregation mode (MAX or SUM)
        min_similarity: Minimum similarity threshold (MAX mode only)
        alpha: g function steepness (SUM mode only)

    Returns:
        List of k selected vehicle indices
    """
    V = Pos.shape[0]  # Total number of vehicles
    M = Pos.shape[1]  # Number of liked features
    N = Neg.shape[1]  # Number of disliked features

    selected_indices = []
    selected_mask = np.zeros(V, dtype=bool)  # Track selected vehicles

    logger.info(f"Starting greedy selection ({mode.value} mode): selecting {k} from {V} vehicles")
    logger.info(f"Pos: {Pos.shape}, Neg: {Neg.shape}, λ={lambda_risk}, μ={mu:.4f}")

    if mode == AggregationMode.MAX:
        # Vectorized MAX mode
        # Filter scores: only count > min_similarity
        Pos_filtered = np.where(Pos > min_similarity, Pos, 0.0)
        Neg_filtered = np.where(Neg > min_similarity, Neg, 0.0)

        # Initialize current max arrays (all zeros initially)
        current_max_pos = np.zeros(M)  # max Pos across selected vehicles for each preference
        current_max_neg = np.zeros(N)  # max Neg across selected vehicles for each risk signal

        # Current coverage and risk
        current_coverage = 0.0
        current_risk = 0.0

        for iteration in range(min(k, V)):
            # Compute marginal gains for ALL remaining vehicles at once
            # new_max_pos[v, m] = max(current_max_pos[m], Pos_filtered[v, m])
            new_max_pos = np.maximum(Pos_filtered, current_max_pos)  # (V, M)
            new_max_neg = np.maximum(Neg_filtered, current_max_neg)  # (V, N)

            # Coverage gain for each vehicle
            new_coverage = np.sum(new_max_pos, axis=1)  # (V,)
            coverage_gains = new_coverage - current_coverage  # (V,)

            # Risk gain for each vehicle
            new_risk = np.sum(new_max_neg, axis=1)  # (V,)
            risk_gains = new_risk - current_risk  # (V,)

            # Total marginal gain
            marginal_gains = coverage_gains - lambda_risk * risk_gains + mu * soft_bonus  # (V,)

            # Mask out already selected vehicles
            marginal_gains[selected_mask] = float('-inf')

            # Select best vehicle
            best_idx = int(np.argmax(marginal_gains))
            best_gain = marginal_gains[best_idx]

            # Update state
            selected_indices.append(best_idx)
            selected_mask[best_idx] = True
            current_max_pos = np.maximum(current_max_pos, Pos_filtered[best_idx])
            current_max_neg = np.maximum(current_max_neg, Neg_filtered[best_idx])
            current_coverage = np.sum(current_max_pos)
            current_risk = np.sum(current_max_neg)

            if iteration < 5 or iteration == k - 1:
                logger.debug(f"Iteration {iteration + 1}/{k}: selected vehicle {best_idx} "
                            f"(gain={best_gain:.4f})")

    else:
        # Vectorized SUM mode (noisy-or coverage)
        # Initialize Q_u for noisy-or tracking
        Q_u = np.ones(M)

        # Precompute g(Pos) for all vehicles
        g_Pos = g_function(Pos, alpha)  # (V, M)

        # Precompute risk penalties (modular, so constant per vehicle)
        risk_penalties = np.sum(h_function(Neg), axis=1)  # (V,)

        for iteration in range(min(k, V)):
            # Coverage gain for each vehicle: Σ_u Q_u · g(Pos(u,v))
            coverage_gains = g_Pos @ Q_u  # (V,) - matrix-vector product

            # Total marginal gain
            marginal_gains = coverage_gains - lambda_risk * risk_penalties + mu * soft_bonus  # (V,)

            # Mask out already selected vehicles
            marginal_gains[selected_mask] = float('-inf')

            # Select best vehicle
            best_idx = int(np.argmax(marginal_gains))
            best_gain = marginal_gains[best_idx]

            # Update state
            selected_indices.append(best_idx)
            selected_mask[best_idx] = True

            # Update Q_u: Q_u = Q_u * (1 - g(Pos(u, v*)))
            Q_u = Q_u * (1 - g_Pos[best_idx])

            if iteration < 5 or iteration == k - 1:
                logger.debug(f"Iteration {iteration + 1}/{k}: selected vehicle {best_idx} "
                            f"(gain={best_gain:.4f})")

    logger.info(f"Greedy selection complete: selected {len(selected_indices)} vehicles")

    return selected_indices


def build_soft_constraints_from_relaxation(
    relaxation_state: Dict[str, Any],
    explicit_filters: Dict[str, Any]
) -> List[SoftConstraint]:
    """
    Build soft constraints from relaxed hard filters.

    When a hard constraint is relaxed during progressive filter relaxation,
    it becomes a soft constraint that provides bonus points for satisfaction.

    Args:
        relaxation_state: Dict with relaxed_filters, original_values, etc.
        explicit_filters: Original explicit filters

    Returns:
        List of SoftConstraint objects
    """
    soft_constraints = []

    relaxed_filters = relaxation_state.get("relaxed_filters", [])
    original_values = relaxation_state.get("original_values", {})

    # Weight tiers: must-have filters get higher weight
    relaxed_inferred = set(relaxation_state.get("relaxed_inferred", []))
    relaxed_regular = set(relaxation_state.get("relaxed_regular", []))
    unmet_must_haves = set(relaxation_state.get("unmet_must_haves", []))

    for filter_name in relaxed_filters:
        original_value = original_values.get(filter_name)
        if original_value is None:
            continue

        # Determine weight based on filter tier
        if filter_name in unmet_must_haves:
            weight = 2.0  # Must-have: highest weight
        elif filter_name in relaxed_regular:
            weight = 1.0  # Regular filter
        elif filter_name in relaxed_inferred:
            weight = 0.5  # Inferred: lowest weight
        else:
            weight = 1.0

        # Determine constraint type based on filter name and value
        if filter_name in ["price", "max_price"]:
            soft_constraints.append(SoftConstraint(
                name="price",
                constraint_type="max",
                original_value=original_value,
                weight=weight
            ))
        elif filter_name in ["min_price"]:
            soft_constraints.append(SoftConstraint(
                name="price",
                constraint_type="min",
                original_value=original_value,
                weight=weight
            ))
        elif filter_name in ["mileage", "max_mileage"]:
            soft_constraints.append(SoftConstraint(
                name="mileage",
                constraint_type="max",
                original_value=original_value,
                weight=weight
            ))
        elif filter_name == "year":
            # Year can be a range like "2020-2024" or single value
            if isinstance(original_value, str) and "-" in original_value:
                try:
                    parts = original_value.split("-")
                    min_year = int(parts[0])
                    max_year = int(parts[1])
                    soft_constraints.append(SoftConstraint(
                        name="year",
                        constraint_type="range",
                        original_value=(min_year, max_year),
                        weight=weight
                    ))
                except (ValueError, IndexError):
                    pass
            elif isinstance(original_value, int):
                soft_constraints.append(SoftConstraint(
                    name="year",
                    constraint_type="range",
                    original_value=(original_value, original_value),
                    weight=weight
                ))
        elif filter_name in ["make", "model", "body_type", "fuel_type", "drivetrain", "transmission"]:
            # Categorical constraints
            if isinstance(original_value, list):
                soft_constraints.append(SoftConstraint(
                    name=filter_name,
                    constraint_type="categorical",
                    original_value=set(original_value),
                    weight=weight
                ))
            else:
                soft_constraints.append(SoftConstraint(
                    name=filter_name,
                    constraint_type="categorical",
                    original_value={original_value},
                    weight=weight
                ))
        elif filter_name == "distance":
            # Distance constraint (max miles from user)
            soft_constraints.append(SoftConstraint(
                name="distance_miles",
                constraint_type="max",
                original_value=original_value,
                weight=weight
            ))

    logger.info(f"Built {len(soft_constraints)} soft constraints from {len(relaxed_filters)} relaxed filters")
    for sc in soft_constraints:
        logger.debug(f"  - {sc.name} ({sc.constraint_type}): {sc.original_value} [weight={sc.weight}]")

    return soft_constraints


def rank_vehicles_by_alignment(
    vehicles: List[Dict],
    phrase_store: PhraseStore,
    implicit_preferences: Dict[str, Any],
    k: int = 20,
    lambda_risk: float = 0.5,
    mode: str = "max",
    min_similarity: float = 0.5,
    tau: float = 0.5,
    alpha: float = 1.0,
    relaxation_state: Optional[Dict[str, Any]] = None,
    explicit_filters: Optional[Dict[str, Any]] = None,
    mu: Optional[float] = None,
    rho: float = 1.0
) -> List[Dict]:
    """
    Rank vehicles using Method 3: Coverage-Risk Optimization.

    Args:
        vehicles: List of vehicle dicts (must pass explicit filters)
        phrase_store: Pre-loaded phrase store
        implicit_preferences: User's implicit preferences
        k: Number of vehicles to recommend
        lambda_risk: Risk penalty weight
        mode: "max" (original) or "sum" (proposed noisy-or)
        min_similarity: Minimum similarity threshold (max mode greedy selection)
        tau: Similarity threshold φ(t) = max(0, t - τ) at phrase level (default 0.5)
        alpha: g function steepness (sum mode)
        relaxation_state: Dict with relaxed filters info (for soft constraints)
        explicit_filters: Original explicit filters (for soft constraints)
        mu: Soft bonus weight (None = auto-calibrate)
        rho: Scale factor for μ calibration

    Returns:
        List of top-k ranked vehicles (same dicts, reordered)
    """
    if not vehicles:
        logger.warning("No vehicles to rank")
        return []

    if not implicit_preferences.get("liked_features") and not implicit_preferences.get("disliked_features"):
        logger.warning("No implicit preferences - returning vehicles in original order")
        return vehicles[:k]

    # Parse mode
    agg_mode = AggregationMode.SUM if mode.lower() == "sum" else AggregationMode.MAX

    # Step 1: Compute alignment matrices
    logger.info(f"Computing alignment scores for {len(vehicles)} vehicles ({agg_mode.value} mode)...")
    Pos, Neg, liked_features, disliked_features = compute_alignment_matrix(
        vehicles,
        phrase_store,
        implicit_preferences,
        mode=agg_mode,
        tau=tau
    )

    # Step 2: Build soft constraints from relaxed filters
    soft_constraints = []
    if relaxation_state and explicit_filters:
        soft_constraints = build_soft_constraints_from_relaxation(
            relaxation_state,
            explicit_filters
        )

    # Step 3: Compute soft bonus vector
    soft_bonus = compute_soft_bonus_vector(vehicles, soft_constraints)

    # Step 4: Calibrate μ if not provided
    if mu is None:
        if np.any(soft_bonus > 0):
            mu = calibrate_mu(Pos, soft_bonus, mode=agg_mode, alpha=alpha, rho=rho)
        else:
            mu = 0.0

    # Step 5: Greedy selection
    logger.info(f"Running greedy coverage-risk optimization (k={k}, λ={lambda_risk}, μ={mu:.4f})...")
    selected_indices = greedy_select_vehicles(
        Pos, Neg, soft_bonus,
        k=k,
        lambda_risk=lambda_risk,
        mu=mu,
        mode=agg_mode,
        min_similarity=min_similarity,
        alpha=alpha
    )

    # Step 6: Reorder vehicles by selection order
    ranked_vehicles = [vehicles[idx] for idx in selected_indices]

    # Step 7: Add alignment scores to vehicles
    for i, idx in enumerate(selected_indices):
        ranked_vehicles[i]["_method3_pos_score"] = float(np.sum(Pos[idx]))
        ranked_vehicles[i]["_method3_neg_score"] = float(np.sum(Neg[idx]))
        ranked_vehicles[i]["_method3_rank"] = i + 1

    logger.info(f"Ranked {len(ranked_vehicles)} vehicles by alignment ({agg_mode.value} mode)")

    return ranked_vehicles
