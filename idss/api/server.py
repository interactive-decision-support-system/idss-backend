"""
FastAPI server for the Simplified IDSS.

Provides REST API endpoints for UI and simulator integration.

Usage:
    python -m idss.api.server
    # or
    uvicorn idss.api.server:app --reload --port 8000
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Optional
import uuid
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from idss.api.models import (
    ChatRequest,
    ChatResponse,
    SessionResponse,
    ResetRequest,
    ResetResponse,
    RecommendRequest,
    RecommendResponse,
    HealthResponse,
)
from idss.core.controller import IDSSController, SessionState
from idss.core.config import get_config, IDSSConfig
from idss.data.vehicle_store import LocalVehicleStore
from idss.diversification.entropy import select_diversification_dimension
from idss.diversification.bucketing import diversify_with_entropy_bucketing
from idss.recommendation.embedding_similarity import rank_with_embedding_similarity
from idss.recommendation.coverage_risk import rank_with_coverage_risk
from idss.utils.logger import get_logger
from idss.core.preload import preload_all, preload_for_method

logger = get_logger("api.server")

# Conversation logging for production
CONVERSATION_LOG_DIR = Path(os.getenv("CONVERSATION_LOG_DIR", "logs/sessions"))
CONVERSATION_LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_conversation(
    session_id: str,
    user_message: str,
    response_type: str,
    response_message: str,
    filters: dict,
    recommendations: list = None,
    bucket_labels: list = None,
    diversification_dimension: str = None,
):
    """Log conversation turn to per-session JSONL file."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "user_message": user_message,
        "response_type": response_type,
        "response_message": response_message,
        "filters_extracted": filters,
    }

    # Add recommendation details if present
    if recommendations:
        buckets_summary = []
        total_count = 0
        for bucket_idx, bucket in enumerate(recommendations):
            bucket_label = bucket_labels[bucket_idx] if bucket_labels and bucket_idx < len(bucket_labels) else f"Bucket {bucket_idx + 1}"
            vehicles_in_bucket = []
            for vehicle in bucket:
                v = vehicle.get("vehicle", vehicle)
                vehicles_in_bucket.append({
                    "vin": vehicle.get("vin"),
                    "year": v.get("year"),
                    "make": v.get("make"),
                    "model": v.get("model"),
                    "trim": v.get("trim"),
                    "price": vehicle.get("retailListing", {}).get("price") or vehicle.get("price"),
                    "mileage": vehicle.get("retailListing", {}).get("mileage") or vehicle.get("mileage"),
                })
            buckets_summary.append({
                "bucket_label": bucket_label,
                "vehicles": vehicles_in_bucket,
            })
            total_count += len(vehicles_in_bucket)
        log_entry["recommendations"] = buckets_summary
        log_entry["recommendations_count"] = total_count
        log_entry["diversification_dimension"] = diversification_dimension

    # Log to session-specific file
    session_log_file = CONVERSATION_LOG_DIR / f"{session_id}.jsonl"
    try:
        with open(session_log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write conversation log: {e}")

    # Also log to stdout for Digital Ocean's logging
    logger.info(f"CONVERSATION [{session_id}]: {json.dumps(log_entry)}")


# Initialize FastAPI app
app = FastAPI(
    title="IDSS API",
    description="Simplified Interactive Decision Support System API",
    version="1.0.0"
)


# Track preload status
_preload_timings: dict = {}


@app.on_event("startup")
async def startup_event():
    """Preload all heavy resources at server startup."""
    global _preload_timings

    # Check if preloading is disabled via environment variable
    skip_preload = os.environ.get("IDSS_SKIP_PRELOAD", "").lower() in ("1", "true", "yes")

    if skip_preload:
        logger.info("Preloading SKIPPED (IDSS_SKIP_PRELOAD=1)")
        _preload_timings = {"skipped": True}
        return

    logger.info("Server starting up - preloading resources...")
    config = get_config()

    # Preload based on configured method
    # Set preload_all_methods=True to load both methods (useful if you switch methods at runtime)
    preload_all_methods = os.environ.get("IDSS_PRELOAD_ALL", "1").lower() in ("1", "true", "yes")

    if preload_all_methods:
        _preload_timings = preload_all(
            preload_embedding_similarity=True,
            preload_coverage_risk=True,
            preload_database=True
        )
    else:
        # Only preload the configured method
        _preload_timings = preload_for_method(config.recommendation_method)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session storage: session_id -> IDSSController
sessions: Dict[str, IDSSController] = {}


def get_or_create_session(
    session_id: Optional[str] = None,
    k: Optional[int] = None,
    method: Optional[str] = None,
    n_rows: Optional[int] = None,
    n_per_row: Optional[int] = None
) -> tuple[str, IDSSController]:
    """Get existing session or create new one with optional config overrides."""
    if session_id and session_id in sessions:
        controller = sessions[session_id]
        # Update config on existing session if overrides provided
        if k is not None:
            controller.config.k = k
        if method is not None:
            controller.config.recommendation_method = method
        if n_rows is not None:
            controller.config.num_rows = n_rows
        if n_per_row is not None:
            controller.config.n_vehicles_per_row = n_per_row
        return session_id, controller

    # Create new session with config overrides
    new_session_id = session_id or str(uuid.uuid4())
    config = get_config()

    # Apply overrides
    if k is not None:
        config.k = k
    if method is not None:
        config.recommendation_method = method
    if n_rows is not None:
        config.num_rows = n_rows
    if n_per_row is not None:
        config.n_vehicles_per_row = n_per_row

    sessions[new_session_id] = IDSSController(config)
    logger.info(f"Created new session: {new_session_id} (k={config.k}, method={config.recommendation_method})")
    return new_session_id, sessions[new_session_id]


# API Endpoints

@app.get("/", response_model=HealthResponse)
async def root():
    """Health check endpoint."""
    config = get_config()
    return HealthResponse(
        status="online",
        service="IDSS API",
        version="1.0.0",
        config={
            "k": config.k,
            "method": config.recommendation_method,
            "n_rows": config.num_rows,
            "n_per_row": config.n_vehicles_per_row,
        }
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Main conversation endpoint.

    Handles user messages, runs interview or generates recommendations.

    Config overrides (optional):
    - k: Number of interview questions (0 = skip interview)
    - method: 'embedding_similarity' or 'coverage_risk'
    - n_rows: Number of result rows
    - n_per_row: Vehicles per row
    """
    try:
        session_id, controller = get_or_create_session(
            session_id=request.session_id,
            k=request.k,
            method=request.method,
            n_rows=request.n_rows,
            n_per_row=request.n_per_row
        )

        # Process the user's message
        response = controller.process_input(request.message)

        # Log conversation for analytics
        log_conversation(
            session_id=session_id,
            user_message=request.message,
            response_type=response.response_type,
            response_message=response.message,
            filters=response.filters_extracted or {},
            recommendations=response.recommendations,
            bucket_labels=response.bucket_labels,
            diversification_dimension=response.diversification_dimension,
        )

        return ChatResponse(
            response_type=response.response_type,
            message=response.message,
            session_id=session_id,
            quick_replies=response.quick_replies,
            recommendations=response.recommendations,
            bucket_labels=response.bucket_labels,
            diversification_dimension=response.diversification_dimension,
            filters=response.filters_extracted or {},
            preferences=response.preferences_extracted or {},
            question_count=controller.state.question_count,
        )

    except Exception as e:
        import traceback
        logger.error(f"Error in /chat: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Get current session state."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    controller = sessions[session_id]
    state = controller.state

    return SessionResponse(
        session_id=session_id,
        filters=state.explicit_filters,
        preferences=state.implicit_preferences,
        question_count=state.question_count,
        conversation_history=state.conversation_history,
    )


@app.post("/session/reset", response_model=ResetResponse)
async def reset_session(request: ResetRequest):
    """Reset session or create new one."""
    session_id = request.session_id or str(uuid.uuid4())

    # Create fresh controller
    config = get_config()
    sessions[session_id] = IDSSController(config)
    logger.info(f"Reset session: {session_id}")

    return ResetResponse(
        session_id=session_id,
        status="reset"
    )


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    if session_id in sessions:
        del sessions[session_id]
        logger.info(f"Deleted session: {session_id}")
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="Session not found")


@app.get("/sessions")
async def list_sessions():
    """List all active sessions."""
    return {
        "active_sessions": len(sessions),
        "session_ids": list(sessions.keys())
    }


@app.get("/status")
async def get_status():
    """Get server status including preload timings."""
    config = get_config()
    return {
        "status": "online",
        "config": {
            "k": config.k,
            "method": config.recommendation_method,
            "n_rows": config.num_rows,
            "n_per_row": config.n_vehicles_per_row,
        },
        "preload": _preload_timings,
        "active_sessions": len(sessions),
    }


@app.post("/recommend", response_model=RecommendResponse)
async def recommend(request: RecommendRequest):
    """
    Direct recommendation endpoint (bypass interview).

    Use this for:
    - Simulator testing with known filters/preferences
    - UI components that need recommendations without chat
    - Batch evaluation
    """
    try:
        session_id = request.session_id or str(uuid.uuid4())
        config = get_config()

        # Override method if specified
        method = request.method or config.recommendation_method

        # Get vehicle store
        store = LocalVehicleStore(require_photos=True)

        # Build filters for SQL query
        db_filters = dict(request.filters) if request.filters else {}
        if not db_filters:
            db_filters['year'] = '2018-2025'

        # Step 1: Get candidates from database
        candidates = store.search_listings(
            filters=db_filters,
            limit=500,
            order_by="price",
            order_dir="ASC"
        )

        if not candidates:
            raise HTTPException(status_code=404, detail="No vehicles found matching filters")

        total_candidates = len(candidates)
        logger.info(f"Found {total_candidates} candidates from SQL")

        # Step 2: Rank with selected method
        if method == "embedding_similarity":
            ranked = rank_with_embedding_similarity(
                vehicles=candidates,
                explicit_filters=request.filters,
                implicit_preferences=request.preferences,
                top_k=100,
                lambda_param=config.embedding_similarity_lambda_param,
                use_mmr=True
            )
        elif method == "coverage_risk":
            ranked = rank_with_coverage_risk(
                vehicles=candidates,
                explicit_filters=request.filters,
                implicit_preferences=request.preferences,
                top_k=100,
                lambda_risk=config.coverage_risk_lambda_risk,
                mode=config.coverage_risk_mode,
                tau=config.coverage_risk_tau,
                alpha=config.coverage_risk_alpha
            )
        else:
            ranked = candidates[:100]

        logger.info(f"Ranked to {len(ranked)} candidates using {method}")

        # Step 3: Select diversification dimension
        div_dimension = select_diversification_dimension(
            candidates=ranked,
            explicit_filters=request.filters
        )

        # Step 4: Bucket vehicles
        buckets, bucket_labels, _ = diversify_with_entropy_bucketing(
            vehicles=ranked,
            dimension=div_dimension,
            n_rows=request.n_rows,
            n_per_row=request.n_per_row
        )

        return RecommendResponse(
            session_id=session_id,
            recommendations=buckets,
            bucket_labels=bucket_labels,
            diversification_dimension=div_dimension,
            total_candidates=total_candidates,
            method_used=method
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error in /recommend: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recommend/compare")
async def compare_methods(request: RecommendRequest):
    """
    Compare Embedding Similarity vs Coverage-Risk results.

    Returns recommendations from both methods for comparison.
    """
    try:
        config = get_config()
        store = LocalVehicleStore(require_photos=True)

        # Build filters
        db_filters = dict(request.filters) if request.filters else {'year': '2018-2025'}

        # Get candidates
        candidates = store.search_listings(
            filters=db_filters,
            limit=500,
            order_by="price",
            order_dir="ASC"
        )

        if not candidates:
            raise HTTPException(status_code=404, detail="No vehicles found")

        results = {}

        for method in ["embedding_similarity", "coverage_risk"]:
            # Rank
            if method == "embedding_similarity":
                ranked = rank_with_embedding_similarity(
                    vehicles=candidates,
                    explicit_filters=request.filters,
                    implicit_preferences=request.preferences,
                    top_k=100,
                    lambda_param=config.embedding_similarity_lambda_param,
                    use_mmr=True
                )
            else:
                ranked = rank_with_coverage_risk(
                    vehicles=candidates,
                    explicit_filters=request.filters,
                    implicit_preferences=request.preferences,
                    top_k=100,
                    lambda_risk=config.coverage_risk_lambda_risk,
                    mode=config.coverage_risk_mode,
                    tau=config.coverage_risk_tau,
                    alpha=config.coverage_risk_alpha
                )

            # Diversify
            div_dimension = select_diversification_dimension(
                candidates=ranked,
                explicit_filters=request.filters
            )

            buckets, bucket_labels, _ = diversify_with_entropy_bucketing(
                vehicles=ranked,
                dimension=div_dimension,
                n_rows=request.n_rows,
                n_per_row=request.n_per_row
            )

            results[method] = {
                "recommendations": buckets,
                "bucket_labels": bucket_labels,
                "diversification_dimension": div_dimension,
            }

        return {
            "total_candidates": len(candidates),
            "filters": request.filters,
            "preferences": request.preferences,
            "embedding_similarity": results["embedding_similarity"],
            "coverage_risk": results["coverage_risk"],
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error in /recommend/compare: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("IDSS API Server")
    print("=" * 60)
    print("API Documentation: http://localhost:8000/docs")
    print("Status endpoint:   http://localhost:8000/status")
    print("")
    print("Environment variables:")
    print("  IDSS_SKIP_PRELOAD=1   - Skip preloading (faster startup, slow first request)")
    print("  IDSS_PRELOAD_ALL=0    - Only preload configured method")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000)
