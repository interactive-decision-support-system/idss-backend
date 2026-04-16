"""
FastAPI router for the LLM shopping agent prototype.

Mount this next to the legacy `/chat` endpoint (e.g. in `mcp-server/app/main.py`):

    from shopping_agent_llm.api import router as llm_router
    app.include_router(llm_router)

Routes:

    POST /chat/llm           — one conversational turn
    POST /chat/llm/reset     — drop a session
    GET  /chat/llm/state/{session_id} — debug peek at stored state

Response shape is modeled after `idss.api.models.ChatResponse` so the
existing frontend at `idss-web/src/app/api/chat/route.ts` renders without
code changes — flip `NEXT_PUBLIC_API_BASE_URL` + the backend path or add an
env var pointing at `/chat/llm` and the same components (question bubble,
product grid, quick-reply chips) light up.

Debug fields (structured_query, latency_ms) are only included when the
request has `?debug=1` so the client view stays clean by default.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from shopping_agent_llm.graph import run_turn
from shopping_agent_llm.harness import get_session_store
from shopping_agent_llm.schema import TurnAction, TurnResult


router = APIRouter(prefix="/chat/llm", tags=["shopping-agent-llm"])


class ChatLLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    session_id: Optional[str] = None


class ChatLLMResponse(BaseModel):
    """Mirrors idss.api.models.ChatResponse. Extra fields returned under
    a `debug` key only when `?debug=1`."""
    model_config = ConfigDict(extra="forbid")

    response_type: str = Field(..., description="'question' or 'recommendations'")
    message: str
    session_id: str

    quick_replies: Optional[List[str]] = None

    recommendations: Optional[List[List[Dict[str, Any]]]] = None
    bucket_labels: Optional[List[str]] = None
    diversification_dimension: Optional[str] = None

    filters: Dict[str, Any] = Field(default_factory=dict)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    question_count: int = 0
    domain: Optional[str] = None

    debug: Optional[Dict[str, Any]] = None


def _product_dict(offer) -> Dict[str, Any]:
    """Shape each product dict with both price_cents and a float `price`
    (in dollars) so either the new or the legacy card component reads it
    without a conditional."""
    p = offer.product.model_dump()
    if p.get("price_cents") is not None:
        p["price"] = p["price_cents"] / 100.0
    p["merchant_id"] = offer.merchant_id
    if offer.rationale:
        p["rationale"] = offer.rationale
    return p


def _question_count(result: TurnResult) -> int:
    # Approximation: count assistant turns so far. The legacy agent tracked
    # only interview-phase questions, but the UI uses this as a progress
    # hint so any monotonic counter works.
    return sum(1 for t in result.state.history if t.role.value == "assistant")


def _to_chat_response(result: TurnResult, include_debug: bool) -> ChatLLMResponse:
    is_question = result.action == TurnAction.ASK_CLARIFIER
    recs: Optional[List[List[Dict[str, Any]]]] = None
    bucket_labels: Optional[List[str]] = None

    if result.offers:
        recs = [[_product_dict(o) for o in result.offers]]
        bucket_labels = ["Top picks"]

    debug = None
    if include_debug:
        debug = {
            "action": result.action.value,
            "latency_ms": result.latency_ms,
            "structured_query": (
                result.structured_query.model_dump()
                if result.structured_query
                else None
            ),
            "slots": dict(result.state.slots),
            "domain": result.state.domain,
            "shown_count": len(result.state.shown_product_ids),
        }

    return ChatLLMResponse(
        response_type="question" if is_question else "recommendations",
        message=result.reply,
        session_id=result.state.session_id,
        quick_replies=result.quick_replies or None,
        recommendations=recs,
        bucket_labels=bucket_labels,
        diversification_dimension=None,
        filters=dict(result.state.slots),
        preferences={},
        question_count=_question_count(result),
        domain=result.state.domain,
        debug=debug,
    )


@router.post("", response_model=ChatLLMResponse, response_model_exclude_none=True)
async def chat_llm(
    req: ChatLLMRequest,
    debug: int = Query(0, description="Set to 1 to include latency + query echo."),
) -> ChatLLMResponse:
    session_id = req.session_id or str(uuid.uuid4())
    result = await run_turn(session_id=session_id, utterance=req.message)
    return _to_chat_response(result, include_debug=bool(debug))


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


@router.post("/reset")
async def reset(req: ResetRequest) -> Dict[str, str]:
    store = get_session_store()
    if hasattr(store, "_store"):
        store._store.pop(req.session_id, None)
    return {"session_id": req.session_id, "status": "reset"}


@router.get("/state/{session_id}")
async def peek_state(session_id: str) -> Dict[str, Any]:
    store = get_session_store()
    state = store.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="session not found")
    return state.model_dump()
