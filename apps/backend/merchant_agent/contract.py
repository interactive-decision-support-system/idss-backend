"""
Shopping-agent ↔ merchant-agent contract.

Single stable surface between the two modules, per ARCHITECTURE.md. Kept
deliberately small so the shopping agent cannot grow dependencies on
merchant internals (KG, vector, SQL, ranking) — it only knows about
StructuredQuery and Offer.

Location note: the ARCHITECTURE.md target location is
`merchant_agent/contract.py`. File moves are ON HOLD; this module lives in
`apps/backend/app/` for now so both sides can import `from merchant_agent.contract import ...`
without a repo-wide restructure. Move later.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from merchant_agent.schemas import ProductSummary


class StructuredQuery(BaseModel):
    """
    Input to a merchant agent. Constructed by the shopping agent from the
    user's interview state + extracted filters.

    Intentionally flat: the shopping agent doesn't need to know how the
    merchant will use these fields (what gets hard-filtered, what gets
    scored softly). That's a merchant decision.
    """
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(..., description="Vertical: 'fashion', 'groceries', 'electronics', ...")
    hard_filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Must-match constraints (e.g. price_max_cents, category, product_type, in_stock).",
    )
    soft_preferences: Dict[str, Any] = Field(
        default_factory=dict,
        description="Weighted preferences the merchant should score against (e.g. brand, color, style).",
    )
    user_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque session/behavioral context. The merchant may ignore it.",
    )
    top_k: int = Field(10, ge=1, le=100, description="Number of offers to return.")


class Offer(BaseModel):
    """
    Output from a merchant agent — one per candidate product. The shopping
    agent treats this as an opaque ranked payload; only `.product` fields
    are rendered for the user.
    """
    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(..., description="Identifier of the merchant that produced this offer.")
    product_id: str
    score: float = Field(..., description="Merchant's relevance score for this offer.")
    score_breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-signal contribution to `score`. For debugging and future aggregator re-ranking.",
    )
    product: ProductSummary
    rationale: str = Field("", description="Short natural-language justification for the user.")
