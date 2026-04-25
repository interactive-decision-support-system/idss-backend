"""
SPIKE — composer feedback loop routing scaffold.

Vision-bullet-1: introduce more looping in the enrichment pipeline.
Issue #85 capability gap 1: no closed-loop refinement.

Today's flow (one-shot):
    specialist → asks 7 buyer questions → composer drops unanswered ones

Target flow (closed loop):
    specialist → asks 7 buyer questions
                            │
                            ▼
                    composer encounters unanswered Q
                            │
                            ▼
              ┌──────── route_question ─────────┐
              ▼                ▼                ▼
        scraper.try_url   web_search    kg_lookup
              │                │                │
              └──────────► evidence ◄───────────┘
                            │
                            ▼
                    composer re-attempts

This module defines the routing interface ONLY. Live wiring into composer is
deferred — see PR description for what's NOT done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable


class QuestionKind(str, Enum):
    SPEC_FACT = "spec_fact"         # numeric or categorical product attribute (refresh_rate, ram_gb)
    BRAND_FACT = "brand_fact"       # who-makes-this, when-released — likely web search
    USAGE_OPINION = "usage_opinion" # subjective, "good for X" — likely review aggregation
    AVAILABILITY = "availability"   # in-stock, where-to-buy
    UNKNOWN = "unknown"


class EvidenceSource(str, Enum):
    SCRAPER = "scraper"
    WEB_SEARCH = "web_search"
    KG_LOOKUP = "kg_lookup"
    REVIEW_AGGREGATOR = "review_aggregator"


@dataclass
class UnansweredQuestion:
    """One specialist-grounded question composer couldn't answer from raw."""

    key: str                           # canonical attribute name (e.g. "refresh_rate")
    text: str                          # human-readable question
    kind: QuestionKind
    product_id: str
    product_url: str | None = None     # if known (raw catalog has it)
    raw_context: dict[str, Any] = field(default_factory=dict)  # raw_attributes slice relevant to this Q


@dataclass
class Evidence:
    """A claim with linked source — vision bullet 2 ('require linked source')."""

    key: str
    value: Any
    source: EvidenceSource
    source_url: str | None = None
    confidence: float = 0.5
    raw_extract: str | None = None     # the snippet evidence came from


@runtime_checkable
class EvidenceProvider(Protocol):
    """Each downstream tool implements this interface.

    Note: the codebase's agents use BaseEnrichmentAgent (ABC). Protocol is used
    here deliberately — providers (scraper, web_search, kg_lookup) are not
    enrichment agents and shouldn't inherit the agent lifecycle. Duck-typing
    suits this lightweight dispatch surface.
    """

    def can_answer(self, question: UnansweredQuestion) -> bool:
        """Return True if this provider is able to attempt this question."""
        ...

    def fetch(self, question: UnansweredQuestion) -> Optional[Evidence]:
        """Attempt to answer the question. Return None if unsuccessful."""
        ...


# ---------------------------------------------------------------------------
# Stub providers — NOT WIRED — document the interface; implementations elsewhere
# ---------------------------------------------------------------------------

class _ScraperProvider:
    """Calls scraper_v1 with a target URL + the specific question. Stub for spike."""

    def can_answer(self, question: UnansweredQuestion) -> bool:
        return (
            question.product_url is not None
            and question.kind in {QuestionKind.SPEC_FACT, QuestionKind.AVAILABILITY}
        )

    def fetch(self, question: UnansweredQuestion) -> Optional[Evidence]:
        return None  # spike-only; real impl delegates to scraper_v1


class _WebSearchProvider:
    """Calls a web search API for facts the catalog page doesn't carry. Stub."""

    def can_answer(self, question: UnansweredQuestion) -> bool:
        return question.kind in {QuestionKind.BRAND_FACT, QuestionKind.SPEC_FACT}

    def fetch(self, question: UnansweredQuestion) -> Optional[Evidence]:
        return None  # spike-only; no external dep added


class _KgLookupProvider:
    """Looks up similar products in the KG to infer missing fields by neighborhood. Stub."""

    def can_answer(self, question: UnansweredQuestion) -> bool:
        return question.kind == QuestionKind.SPEC_FACT

    def fetch(self, question: UnansweredQuestion) -> Optional[Evidence]:
        return None  # spike-only; real impl queries kg_service.py


# Default ordering: scraper first (cheapest + most accurate when URL exists),
# then web_search, then kg_lookup.
_DEFAULT_PROVIDERS: list[EvidenceProvider] = [
    _ScraperProvider(),
    _WebSearchProvider(),
    _KgLookupProvider(),
]


def route_question(
    question: UnansweredQuestion,
    providers: list[EvidenceProvider] | None = None,
) -> Optional[Evidence]:
    """Decide which provider should answer the question and fetch evidence.

    Returns None if no provider can answer or all capable providers return None.

    Dispatch is rule-based today (``EvidenceProvider.can_answer``); a future
    agentic version would let an LLM choose, with retrospective learning from
    past successes per ``(product_type, question.kind)``.

    Fall-through semantics: if the first capable provider returns None (e.g.
    fetch failed), the next capable provider is tried.
    """
    active = providers if providers is not None else _DEFAULT_PROVIDERS
    for provider in active:
        if provider.can_answer(question):
            evidence = provider.fetch(question)
            if evidence is not None:
                return evidence
    return None
