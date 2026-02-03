"""
Vehicle Search Tool - MCP Tool wrapping IDSS recommendation system.

This tool provides access to IDSS's sophisticated vehicle recommendation system:
- SQLite-based vehicle database with ~150k listings
- Sentence embedding similarity ranking (all-mpnet-base-v2)
- Coverage-risk optimization
- Entropy-based diversification bucketing

Usage:
    from app.tools.vehicle_search import search_vehicles, VehicleSearchRequest

    results = search_vehicles(VehicleSearchRequest(
        filters={"make": "Toyota", "body_style": "SUV", "price": "20000-40000"},
        preferences={"liked_features": ["fuel efficiency", "spacious"]},
        n_rows=3,
        n_per_row=3
    ))
"""

import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from pydantic import BaseModel, Field

# Add IDSS to path (parent of mcp-server)
IDSS_ROOT = Path(__file__).parent.parent.parent.parent
if str(IDSS_ROOT) not in sys.path:
    sys.path.insert(0, str(IDSS_ROOT))

from app.structured_logger import StructuredLogger

logger = StructuredLogger("vehicle_search_tool")


# ============================================================================
# Request/Response Models
# ============================================================================

class VehicleSearchRequest(BaseModel):
    """Request model for vehicle search tool."""
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Explicit filters: make, model, year, price, body_style, fuel_type, etc."
    )
    preferences: Dict[str, Any] = Field(
        default_factory=dict,
        description="Implicit preferences: liked_features, disliked_features, use_case, notes"
    )
    method: str = Field(
        default="embedding_similarity",
        description="Ranking method: 'embedding_similarity' or 'coverage_risk'"
    )
    n_rows: int = Field(default=3, description="Number of result rows")
    n_per_row: int = Field(default=3, description="Vehicles per row")
    limit: int = Field(default=500, description="Max candidates from SQL query")


class VehicleSearchResponse(BaseModel):
    """Response model for vehicle search tool."""
    recommendations: List[List[Dict[str, Any]]] = Field(
        description="2D grid of vehicles [rows][vehicles]"
    )
    bucket_labels: List[str] = Field(description="Labels for each row")
    diversification_dimension: str = Field(description="Dimension used for bucketing")
    total_candidates: int = Field(description="Total candidates before ranking")
    method_used: str = Field(description="Ranking method used")


# ============================================================================
# IDSS Components (preloaded at startup for lower latency)
# ============================================================================

_vehicle_store = None
_dense_embedding_store = None
_phrase_embedding_store = None
_sentence_transformer = None
_preloaded = False


def preload_idss_components():
    """
    Preload all IDSS components at server startup.

    This loads:
    - LocalVehicleStore (SQLite connection)
    - SentenceTransformer model (all-mpnet-base-v2)
    - FAISS index for dense embeddings
    - Phrase embeddings for coverage-risk ranking

    Call this from main.py at startup to reduce first-request latency.
    """
    global _vehicle_store, _dense_embedding_store, _sentence_transformer, _preloaded

    if _preloaded:
        logger.info("preload_skip", "IDSS components already preloaded")
        return

    import time
    start_time = time.time()

    logger.info("preload_start", "Preloading IDSS components...")

    # 1. Load vehicle store (SQLite)
    try:
        from idss.data.vehicle_store import LocalVehicleStore
        _vehicle_store = LocalVehicleStore()
        logger.info("preload_vehicle_store", "LocalVehicleStore loaded")
    except Exception as e:
        logger.error("preload_vehicle_store_error", f"Failed to load vehicle store: {e}")

    # 2. Load SentenceTransformer model
    try:
        from sentence_transformers import SentenceTransformer
        _sentence_transformer = SentenceTransformer('all-mpnet-base-v2')
        # Warm up with a test encoding
        _sentence_transformer.encode(["warm up query"])
        logger.info("preload_sentence_transformer", "SentenceTransformer loaded and warmed up")
    except Exception as e:
        logger.error("preload_sentence_transformer_error", f"Failed to load SentenceTransformer: {e}")

    # 3. Load dense embedding store (FAISS)
    try:
        from idss.recommendation.dense_embedding_store import DenseEmbeddingStore
        _dense_embedding_store = DenseEmbeddingStore()
        logger.info("preload_dense_store", "DenseEmbeddingStore loaded")
    except Exception as e:
        logger.warning("preload_dense_store_error", f"Failed to load dense embedding store: {e}")

    # 4. Preload embedding similarity module (triggers internal caching)
    try:
        from idss.recommendation import embedding_similarity
        logger.info("preload_embedding_similarity", "Embedding similarity module loaded")
    except Exception as e:
        logger.warning("preload_embedding_similarity_error", f"Failed to load embedding similarity: {e}")

    elapsed = time.time() - start_time
    _preloaded = True
    logger.info("preload_complete", f"IDSS components preloaded in {elapsed:.2f}s")


def _get_vehicle_store():
    """Get the vehicle store (preloaded or lazy-loaded)."""
    global _vehicle_store
    if _vehicle_store is None:
        try:
            from idss.data.vehicle_store import LocalVehicleStore
            _vehicle_store = LocalVehicleStore()
            logger.info("vehicle_store_loaded", "LocalVehicleStore initialized (lazy)")
        except Exception as e:
            logger.error("vehicle_store_error", f"Failed to load vehicle store: {e}")
            raise
    return _vehicle_store


def _get_dense_embedding_store():
    """Get the dense embedding store (preloaded or lazy-loaded)."""
    global _dense_embedding_store
    if _dense_embedding_store is None:
        try:
            from idss.recommendation.dense_embedding_store import DenseEmbeddingStore
            _dense_embedding_store = DenseEmbeddingStore()
            logger.info("dense_store_loaded", "DenseEmbeddingStore initialized (lazy)")
        except Exception as e:
            logger.error("dense_store_error", f"Failed to load dense embedding store: {e}")
            raise
    return _dense_embedding_store


# ============================================================================
# Filter Normalization
# ============================================================================

def normalize_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize filters from agent format to IDSS format.

    Agent format (from UniversalAgent):
        - use_case: "family"
        - budget: "$20000-$40000" or "Under $30000"
        - brand: "Toyota"

    IDSS format:
        - make: "Toyota"
        - price: "20000-40000"
        - body_style: "SUV"
    """
    normalized = {}

    # Map brand → make
    if filters.get("brand"):
        normalized["make"] = filters["brand"]
    if filters.get("make"):
        normalized["make"] = filters["make"]

    # Map budget → price range
    budget = filters.get("budget", "")
    if budget:
        if isinstance(budget, dict):
            # {"min": 20000, "max": 40000}
            price_min = budget.get("min", "")
            price_max = budget.get("max", "")
            if price_min or price_max:
                normalized["price"] = f"{price_min or 0}-{price_max or 999999}"
        elif isinstance(budget, str):
            # Parse string formats: "$20000-$40000", "Under $30000", "$500-$1000"
            import re
            budget_clean = budget.replace("$", "").replace(",", "").replace(" ", "")

            # Range: "20000-40000"
            range_match = re.match(r"(\d+)-(\d+)", budget_clean)
            if range_match:
                normalized["price"] = f"{range_match.group(1)}-{range_match.group(2)}"
            else:
                # Under X: "under30000", "Under $30000"
                under_match = re.search(r"under(\d+)", budget_clean.lower())
                if under_match:
                    normalized["price"] = f"0-{under_match.group(1)}"
                else:
                    # Over X: "over30000"
                    over_match = re.search(r"over(\d+)", budget_clean.lower())
                    if over_match:
                        normalized["price"] = f"{over_match.group(1)}-999999"

    # Direct price passthrough
    if filters.get("price"):
        normalized["price"] = filters["price"]

    # Map use_case → body_style (heuristic)
    use_case = filters.get("use_case", "").lower()
    if use_case and "body_style" not in filters:
        use_case_to_body = {
            "family": "SUV",
            "commute": "Sedan",
            "commuter": "Sedan",
            "work": "Sedan",
            "adventure": "SUV",
            "off-road": "Truck",
            "hauling": "Truck",
            "luxury": None,  # Don't restrict
            "sports": "Coupe",
            "eco": None,  # Could be any body style with good MPG
        }
        mapped_body = use_case_to_body.get(use_case)
        if mapped_body:
            normalized["body_style"] = mapped_body

    # Pass through other filters directly
    direct_filters = [
        "model", "trim", "year", "body_style", "fuel_type", "drivetrain",
        "transmission", "exterior_color", "interior_color", "is_used", "is_cpo",
        "highway_mpg", "state", "seating_capacity", "doors", "mileage"
    ]
    for key in direct_filters:
        if key in filters and filters[key]:
            normalized[key] = filters[key]

    return normalized


def extract_preferences(filters: Dict[str, Any], preferences: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract implicit preferences from filters and explicit preferences.

    Returns IDSS-compatible preferences dict with:
        - liked_features: List[str]
        - disliked_features: List[str]
        - use_case: str
        - notes: str
    """
    result = {
        "liked_features": [],
        "disliked_features": [],
        "use_case": "",
        "notes": ""
    }

    # Copy explicit preferences
    if preferences.get("liked_features"):
        result["liked_features"] = list(preferences["liked_features"])
    if preferences.get("disliked_features"):
        result["disliked_features"] = list(preferences["disliked_features"])
    if preferences.get("use_case"):
        result["use_case"] = preferences["use_case"]
    if preferences.get("notes"):
        result["notes"] = preferences["notes"]

    # Infer preferences from use_case in filters
    use_case = filters.get("use_case", "").lower()
    if use_case:
        result["use_case"] = use_case

        # Map use_case to liked features
        use_case_features = {
            "family": ["spacious interior", "safety features", "comfortable ride"],
            "commute": ["fuel efficiency", "reliable", "comfortable"],
            "commuter": ["fuel efficiency", "reliable", "comfortable"],
            "work": ["professional appearance", "comfortable", "reliable"],
            "adventure": ["all-wheel drive", "ground clearance", "cargo space"],
            "off-road": ["4x4", "durable", "ground clearance"],
            "luxury": ["premium interior", "advanced features", "comfortable"],
            "sports": ["performance", "handling", "acceleration"],
            "eco": ["fuel efficiency", "hybrid", "low emissions"],
            "budget": ["affordable", "reliable", "low maintenance"],
        }
        inferred = use_case_features.get(use_case, [])
        for feature in inferred:
            if feature not in result["liked_features"]:
                result["liked_features"].append(feature)

    return result


# ============================================================================
# Main Search Function
# ============================================================================

def search_vehicles(request: VehicleSearchRequest) -> VehicleSearchResponse:
    """
    Search for vehicles using IDSS recommendation system.

    This is the main entry point for the vehicle search tool.

    Pipeline:
    1. Normalize filters from agent format to IDSS format
    2. Query SQLite database for candidates
    3. Rank by embedding similarity or coverage-risk
    4. Diversify into buckets using entropy-based bucketing
    5. Return 2D grid of recommendations
    """
    logger.info("search_vehicles_start", "Starting vehicle search", {
        "filters": request.filters,
        "preferences": request.preferences,
        "method": request.method,
        "n_rows": request.n_rows,
        "n_per_row": request.n_per_row,
    })

    try:
        # Step 1: Normalize filters
        normalized_filters = normalize_filters(request.filters)
        preferences = extract_preferences(request.filters, request.preferences)

        logger.info("search_vehicles_normalized", "Filters normalized", {
            "normalized_filters": normalized_filters,
            "preferences": preferences,
        })

        # Step 2: Get candidates from SQL
        store = _get_vehicle_store()
        candidates = store.search_listings(
            filters=normalized_filters,
            limit=request.limit,
            order_by="price",
            order_dir="ASC"
        )

        logger.info("search_vehicles_candidates", f"Found {len(candidates)} candidates", {
            "count": len(candidates),
        })

        if not candidates:
            return VehicleSearchResponse(
                recommendations=[],
                bucket_labels=[],
                diversification_dimension="none",
                total_candidates=0,
                method_used=request.method
            )

        # Step 3: Rank candidates
        if request.method == "embedding_similarity":
            ranked = _rank_by_embedding_similarity(
                candidates, normalized_filters, preferences
            )
        else:
            ranked = _rank_by_coverage_risk(
                candidates, normalized_filters, preferences
            )

        logger.info("search_vehicles_ranked", f"Ranked {len(ranked)} vehicles", {
            "method": request.method,
        })

        # Step 4: Diversify into buckets
        buckets, labels, dimension = _diversify_vehicles(
            ranked, request.n_rows, request.n_per_row
        )

        logger.info("search_vehicles_done", "Search complete", {
            "buckets": len(buckets),
            "dimension": dimension,
        })

        return VehicleSearchResponse(
            recommendations=buckets,
            bucket_labels=labels,
            diversification_dimension=dimension,
            total_candidates=len(candidates),
            method_used=request.method
        )

    except Exception as e:
        logger.error("search_vehicles_error", f"Search failed: {e}", {})
        import traceback
        traceback.print_exc()
        return VehicleSearchResponse(
            recommendations=[],
            bucket_labels=[],
            diversification_dimension="error",
            total_candidates=0,
            method_used=request.method
        )


# ============================================================================
# Ranking Functions
# ============================================================================

def _rank_by_embedding_similarity(
    vehicles: List[Dict[str, Any]],
    filters: Dict[str, Any],
    preferences: Dict[str, Any],
    top_k: int = 100
) -> List[Dict[str, Any]]:
    """Rank vehicles by dense embedding similarity."""
    try:
        from idss.recommendation.embedding_similarity import rank_with_embedding_similarity

        ranked = rank_with_embedding_similarity(
            vehicles=vehicles,
            explicit_filters=filters,
            implicit_preferences=preferences,
            top_k=top_k,
            lambda_param=0.85,  # MMR diversity weight
            use_mmr=True
        )
        return ranked
    except ImportError as e:
        logger.warning("embedding_fallback", f"Embedding ranking not available: {e}")
        # Fallback: return vehicles as-is (sorted by price)
        return vehicles[:top_k]


def _rank_by_coverage_risk(
    vehicles: List[Dict[str, Any]],
    filters: Dict[str, Any],
    preferences: Dict[str, Any],
    top_k: int = 100
) -> List[Dict[str, Any]]:
    """Rank vehicles by coverage-risk optimization."""
    try:
        from idss.recommendation.coverage_risk import rank_with_coverage_risk

        ranked = rank_with_coverage_risk(
            vehicles=vehicles,
            explicit_filters=filters,
            implicit_preferences=preferences,
            top_k=top_k,
            lambda_risk=0.5,
            mode="sum"
        )
        return ranked
    except ImportError as e:
        logger.warning("coverage_risk_fallback", f"Coverage-risk ranking not available: {e}")
        # Fallback to embedding similarity
        return _rank_by_embedding_similarity(vehicles, filters, preferences, top_k)


# ============================================================================
# Diversification
# ============================================================================

def _diversify_vehicles(
    vehicles: List[Dict[str, Any]],
    n_rows: int = 3,
    n_per_row: int = 3
) -> Tuple[List[List[Dict[str, Any]]], List[str], str]:
    """
    Diversify vehicles into buckets using entropy-based bucketing.

    Returns:
        - buckets: 2D grid of vehicles
        - labels: Labels for each row
        - dimension: The dimension used for bucketing
    """
    try:
        from idss.diversification.bucketing import diversify_with_entropy_bucketing

        # Function returns tuple: (buckets, labels, dimension)
        buckets, labels, dimension = diversify_with_entropy_bucketing(
            vehicles=vehicles,
            dimension="price",  # Use price as default dimension
            n_rows=n_rows,
            n_per_row=n_per_row
        )

        return buckets, labels, dimension

    except ImportError as e:
        logger.warning("diversification_fallback", f"Entropy bucketing not available: {e}")

        # Fallback: simple price-based bucketing
        total_needed = n_rows * n_per_row
        vehicles = vehicles[:total_needed]

        # Sort by price (from vehicle.price or retailListing.price)
        def get_price(v):
            if "vehicle" in v:
                return v["vehicle"].get("price", 0)
            return v.get("price", 0)

        vehicles.sort(key=get_price)

        # Split into rows
        buckets = []
        labels = []
        bucket_size = max(1, len(vehicles) // n_rows)

        for i in range(n_rows):
            start = i * bucket_size
            end = start + n_per_row
            if i == n_rows - 1:
                end = min(start + n_per_row, len(vehicles))

            bucket = vehicles[start:end]
            if bucket:
                buckets.append(bucket)

                # Generate label
                prices = [get_price(v) for v in bucket]
                min_p = min(prices) if prices else 0
                max_p = max(prices) if prices else 0

                if i == 0:
                    labels.append(f"Budget (${min_p:,.0f}-${max_p:,.0f})")
                elif i == n_rows - 1:
                    labels.append(f"Premium (${min_p:,.0f}-${max_p:,.0f})")
                else:
                    labels.append(f"Mid-Range (${min_p:,.0f}-${max_p:,.0f})")

        return buckets, labels, "price"
