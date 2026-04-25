"""Pydantic types for the enrichment pipeline.

Every agent takes a ProductInput and returns a StrategyOutput; the runner
wraps both in AgentResult (latency, cost, trace id, error). Catalog-level
artifacts (CatalogSchema, AssessorOutput, OrchestratorPlan, ProposalAck)
sit alongside.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Per-product I/O
# ---------------------------------------------------------------------------


class ProductInput(BaseModel):
    """Single product handed to an enrichment agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    product_id: UUID
    title: str | None = None
    category: str | None = None
    brand: str | None = None
    description: str | None = None
    price: Decimal | None = None
    link: str | None = None
    raw_attributes: dict[str, Any] = Field(default_factory=dict)


class StrategyOutput(BaseModel):
    """Payload an agent writes to merchants.products_enriched_default."""

    product_id: UUID
    strategy: str
    model: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    notes: str | None = None


class AgentResult(BaseModel):
    """Wrapper the runner uses to record one agent invocation."""

    success: bool
    output: StrategyOutput | None = None
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float | None = None
    trace_id: str | None = None
    strategy: str
    product_id: UUID
    # Populated when the agent runs inside a run_context block; None otherwise.
    run_id: str | None = None
    kg_strategy: str | None = None


# ---------------------------------------------------------------------------
# Catalog-level artifacts
# ---------------------------------------------------------------------------


SlotType = Literal["numeric", "categorical", "boolean", "text"]


class SlotSchema(BaseModel):
    """A single discovered slot in the catalog (or one a merchant already knows)."""

    key: str
    type: SlotType
    unit: str | None = None
    enum_values: list[str] | None = None
    value_range: tuple[float, float] | None = None
    fill_rate: float = 0.0
    source_strategies: list[str] = Field(default_factory=list)
    description: str | None = None


class ProductTypeSchema(BaseModel):
    """Per product-type slice of CatalogSchema."""

    product_type: str
    sample_count: int
    common_slots: list[SlotSchema] = Field(default_factory=list)
    summary: str | None = None


class CatalogSchema(BaseModel):
    """Discovered shape of a merchant's catalog. Persisted as JSON; later promotable
    to merchants.catalog_schema_<merchant_id>."""

    merchant_id: str
    generated_at: datetime
    catalog_size: int
    product_types: list[ProductTypeSchema] = Field(default_factory=list)


class AssessorOutput(BaseModel):
    """Catalog profile produced by the assessor. Drives orchestration."""

    catalog_size: int
    domain_distribution: dict[str, float] = Field(default_factory=dict)
    column_density: dict[str, float] = Field(default_factory=dict)
    sparse_attribute_keys: list[str] = Field(default_factory=list)
    discovered_product_types: list[str] = Field(default_factory=list)
    recommended_strategies: list[str] = Field(default_factory=list)
    per_product_recommendations: dict[UUID, list[str]] | None = None
    notes: str | None = None


class OrchestratorPlan(BaseModel):
    """Per-product ordered list of strategies the runner will invoke."""

    per_product_agents: dict[UUID, list[str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# MerchantAgentClient consultation surface
# ---------------------------------------------------------------------------


class ProposalDecision(BaseModel):
    slot: SlotSchema
    decision: Literal["accepted", "deferred", "rejected"]
    reason: str | None = None


class ProposalAck(BaseModel):
    """Result of MerchantAgentClient.propose_schema_extension."""

    merchant_id: str
    decisions: list[ProposalDecision] = Field(default_factory=list)
    proposal_id: str


# ---------------------------------------------------------------------------
# Composer decisions (one row per key the composer considered — kept or
# dropped). Surfaces as the cell-lineage audit log for #81.
# ---------------------------------------------------------------------------


class SourceKind(str, Enum):
    """Provenance taxonomy for a single composed field (vision bullet 3 / #88).

    Enables the coverage dashboard to count what fraction of canonical fields
    come from parsing raw input vs. crawling vs. LLM world knowledge.

    RAW_PARSE            — fact extracted directly from raw catalog input (parser_v1)
    SCRAPE               — fact extracted from a crawled manufacturer page (scraper_v1)
    PARAMETRIC           — LLM produced from world/parametric knowledge
                           (specialist_v1, taxonomy_v1, soft_tagger_v1, or
                           composer alone when no upstream contributed)
    DETERMINISTIC_FALLBACK — rule-based / formula / pass-through; no LLM inference
    """

    RAW_PARSE = "raw_parse"
    SCRAPE = "scrape"
    PARAMETRIC = "parametric"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class ComposerDecision(BaseModel):
    """One entry in ``composer_decisions``. Structured schema lets the
    inspector (#81) render cell lineage without re-parsing free-form JSON."""

    key: str
    chosen_value: Any = None
    source_strategy: str | None = None
    source_kind: SourceKind = SourceKind.PARAMETRIC  # safe default; reconciler overrides per source_strategy
    reason: str | None = None
    dropped_alternatives: list[Any] = Field(default_factory=list)
