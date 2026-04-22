"""Tests for the composer feedback-loop routing scaffold (spike).

Covers routing decisions: provider selection, fall-through, None path, and
Evidence dataclass shape.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

import pytest

from app.enrichment.orchestration.feedback_loop import (
    Evidence,
    EvidenceProvider,
    EvidenceSource,
    QuestionKind,
    UnansweredQuestion,
    route_question,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec_question(*, url: str | None = None) -> UnansweredQuestion:
    return UnansweredQuestion(
        key="refresh_rate",
        text="What is the refresh rate?",
        kind=QuestionKind.SPEC_FACT,
        product_id="prod-001",
        product_url=url,
    )


def _make_evidence(key: str = "refresh_rate", source: EvidenceSource = EvidenceSource.SCRAPER) -> Evidence:
    return Evidence(
        key=key,
        value="144Hz",
        source=source,
        source_url="https://example.com/laptop",
        confidence=0.9,
        raw_extract="144Hz refresh rate",
    )


def _fake_provider(*, can: bool, evidence: Evidence | None) -> EvidenceProvider:
    """Hand-rolled fake — avoids MagicMock spec mismatch on Protocol."""
    class _Fake:
        def can_answer(self, question: UnansweredQuestion) -> bool:
            return can
        def fetch(self, question: UnansweredQuestion) -> Optional[Evidence]:
            return evidence
    return _Fake()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_route_question_picks_scraper_when_url_present_and_spec_fact():
    """URL present + SPEC_FACT → scraper provider is first and wins."""
    expected = _make_evidence(source=EvidenceSource.SCRAPER)
    scraper = _fake_provider(can=True, evidence=expected)
    web = _fake_provider(can=False, evidence=None)
    kg = _fake_provider(can=False, evidence=None)

    result = route_question(_spec_question(url="https://example.com/laptop"), providers=[scraper, web, kg])

    assert result is expected
    assert result.source == EvidenceSource.SCRAPER


def test_route_question_falls_through_to_web_search_when_no_url():
    """No URL → scraper can_answer=False; web_search picks it up."""
    web_evidence = _make_evidence(source=EvidenceSource.WEB_SEARCH)
    scraper = _fake_provider(can=False, evidence=None)
    web = _fake_provider(can=True, evidence=web_evidence)
    kg = _fake_provider(can=False, evidence=None)

    result = route_question(_spec_question(url=None), providers=[scraper, web, kg])

    assert result is web_evidence
    assert result.source == EvidenceSource.WEB_SEARCH


def test_route_question_returns_none_when_no_provider_can_answer():
    """UNKNOWN kind with no URL → no provider claims it → None."""
    question = UnansweredQuestion(
        key="some_unknown",
        text="Is this good for gaming?",
        kind=QuestionKind.UNKNOWN,
        product_id="prod-002",
        product_url=None,
    )
    providers = [
        _fake_provider(can=False, evidence=None),
        _fake_provider(can=False, evidence=None),
    ]

    result = route_question(question, providers=providers)

    assert result is None


def test_route_question_respects_provider_order():
    """When multiple providers can_answer, try first; fall through if it returns None."""
    # First provider can answer but returns None (fetch fails)
    first = _fake_provider(can=True, evidence=None)
    # Second provider can answer and returns evidence
    second_evidence = _make_evidence(source=EvidenceSource.KG_LOOKUP)
    second = _fake_provider(can=True, evidence=second_evidence)
    # Third should never be reached
    third = _fake_provider(can=True, evidence=_make_evidence())

    result = route_question(_spec_question(url="https://example.com"), providers=[first, second, third])

    assert result is second_evidence
    assert result.source == EvidenceSource.KG_LOOKUP


def test_evidence_carries_source_and_url():
    """Smoke test: Evidence dataclass fields round-trip correctly."""
    ev = Evidence(
        key="ram_gb",
        value=16,
        source=EvidenceSource.WEB_SEARCH,
        source_url="https://example.com/spec",
        confidence=0.85,
        raw_extract="16 GB RAM",
    )

    assert ev.key == "ram_gb"
    assert ev.value == 16
    assert ev.source == EvidenceSource.WEB_SEARCH
    assert ev.source_url == "https://example.com/spec"
    assert ev.confidence == 0.85
    assert ev.raw_extract == "16 GB RAM"


def test_route_question_uses_default_providers_when_none_given():
    """Calling without explicit providers uses _DEFAULT_PROVIDERS (all stubs → None)."""
    # All default providers have stub fetch() → None, so result is None
    question = _spec_question(url="https://example.com/laptop")
    result = route_question(question)
    assert result is None


def test_unanswered_question_raw_context_defaults_to_empty():
    """raw_context should default to empty dict, not a shared mutable."""
    q1 = UnansweredQuestion(key="x", text="x?", kind=QuestionKind.SPEC_FACT, product_id="p1")
    q2 = UnansweredQuestion(key="y", text="y?", kind=QuestionKind.BRAND_FACT, product_id="p2")
    q1.raw_context["a"] = 1
    assert q2.raw_context == {}
