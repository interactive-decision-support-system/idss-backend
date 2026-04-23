"""composer_v1 — single writer of the canonical enriched row (issues #83, #88)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.composer import (
    ComposerAgent,
    _STRATEGY_TO_SOURCE_KIND,
    _reconcile_composer_output,
)
from app.enrichment.agents.specialist import SpecialistAgent
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.types import ProductInput, SourceKind


@pytest.fixture(autouse=True)
def _registry_clean():
    registry._reset_for_tests()
    registry.register(ComposerAgent)
    # SpecialistAgent is registered so the composer's narrative-key strip
    # reads a non-empty frozenset from registry.narrative_keys(). Without
    # this, the strip silently no-ops in isolated composer tests.
    registry.register(SpecialistAgent)
    yield
    registry._reset_for_tests()


class _FakeLLM:
    def __init__(self, payload=None, *, raises: Exception | None = None):
        self.payload = payload
        self.raises = raises
        self.calls: list[dict] = []

    def complete(self, **kw):
        self.calls.append(kw)
        if self.raises is not None:
            raise self.raises
        return LLMResponse(
            text="",
            model=kw.get("model") or "gpt-5",
            input_tokens=50,
            output_tokens=50,
            cost_usd=0.001,
            latency_ms=10,
            parsed_json=self.payload,
        )


def _product(**overrides):
    defaults = dict(
        product_id=uuid4(),
        title="ThinkPad X1 Carbon, 16GB RAM, 512GB SSD",
        brand="Lenovo",
        category="Electronics",
        description="Business ultraportable.",
        price=Decimal("1299.00"),
        raw_attributes={"description": "Business ultraportable.", "color": "black"},
    )
    defaults.update(overrides)
    return ProductInput(**defaults)


def _upstream_ctx():
    return {
        "taxonomy": {
            "product_type": "laptop",
            "product_type_confidence": 0.92,
        },
        "parsed": {
            "parsed_specs": {"ram_gb": 16, "storage_gb": 512},
            "parsed_source_fields": {"ram_gb": "title", "storage_gb": "title"},
        },
        "specialist": {
            "specialist_capabilities": ["long battery"],
            "specialist_use_case_fit": {"business": 0.9},
            "specialist_audience": {"professionals": "lightweight"},
            "specialist_buyer_questions": ["What's the warranty?"],
        },
        "soft_tagger": {"good_for_tags": {"good_for_business": 0.9}},
        "scraped": {"scraped_specs": {"weight_kg": 1.12}},
    }


def test_composer_emits_composed_fields_and_decisions():
    llm = _FakeLLM(
        {
            "composed_fields": {
                "product_type": "laptop",
                "ram_gb": 16,
                "storage_gb": 512,
                "good_for_business": 0.9,
            },
            "composer_decisions": [
                {
                    "key": "ram_gb",
                    "chosen_value": 16,
                    "source_strategy": "parser_v1",
                    "reason": "grounded in title",
                    "dropped_alternatives": [],
                }
            ],
        }
    )
    agent = ComposerAgent(llm=llm)
    result = agent.run(_product(), _upstream_ctx())

    assert result.success is True
    attrs = result.output.attributes
    assert attrs["composed_fields"] == {
        "product_type": "laptop",
        "ram_gb": 16,
        "storage_gb": 512,
        "good_for_business": 0.9,
    }
    # The reconciler (#88) ensures all 4 composed keys have decisions; the
    # LLM only provided ram_gb so the other 3 are synthesized.
    decision_keys = {d["key"] for d in attrs["composer_decisions"]}
    assert decision_keys == {"product_type", "ram_gb", "storage_gb", "good_for_business"}
    ram_decision = next(d for d in attrs["composer_decisions"] if d["key"] == "ram_gb")
    assert ram_decision["source_strategy"] == "parser_v1"
    assert "composed_at" in attrs


def test_composer_keeps_canonical_values_even_when_raw_also_has_them():
    """Review fix #1: echoes must stay on the canonical row — downstream
    readers shouldn't have to re-join raw to reconstruct canonical state."""
    product = _product(raw_attributes={"color": "black", "ram_gb": 16})
    llm = _FakeLLM(
        {
            "composed_fields": {"color": "black", "ram_gb": 16},
            "composer_decisions": [
                {
                    "key": "color",
                    "chosen_value": "black",
                    "source_strategy": "parser_v1",
                    "reason": "echoes_raw",
                    "dropped_alternatives": [],
                },
                {
                    "key": "ram_gb",
                    "chosen_value": 16,
                    "source_strategy": "parser_v1",
                    "reason": "echoes_raw",
                    "dropped_alternatives": [],
                },
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(product, _upstream_ctx())
    assert result.output.attributes["composed_fields"] == {"color": "black", "ram_gb": 16}


def test_composer_strips_narrative_keys_via_registry():
    """Narrative-key strip reads from the registry (specialist_v1 self-
    declares NARRATIVE_KEYS), not a hardcoded list."""
    llm = _FakeLLM(
        {
            "composed_fields": {
                "specialist_capabilities": ["long battery"],
                "specialist_audience": {"professionals": "lightweight"},
                "specialist_buyer_questions": ["What's the warranty?"],
                "specialist_use_case_fit": {"business": 0.9},
                "ram_gb": 16,
            },
            "composer_decisions": [],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    # specialist_use_case_fit is NOT narrative (structured), so it survives.
    assert result.output.attributes["composed_fields"] == {
        "specialist_use_case_fit": {"business": 0.9},
        "ram_gb": 16,
    }


def test_composer_skips_llm_when_no_upstream_findings():
    llm = _FakeLLM({"composed_fields": {"oops": "should_not_see_this"}})
    result = ComposerAgent(llm=llm).run(_product(), {})
    assert llm.calls == []
    assert result.output.attributes["composed_fields"] == {}
    assert result.output.notes == "no_upstream_findings"


def test_composer_uses_json_mode_and_composer_model_default():
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    assert llm.calls[0]["json_mode"] is True
    assert llm.calls[0]["model"] == "gpt-5"
    # Raised to 16000 (PR #103 set 6000 for gpt-5-mini; composer uses gpt-5
    # full tier whose reasoning floor alone is ≥6000 tokens on hard products —
    # 7/10 calls on a mocklaptops batch hit the 6000 ceiling with empty output).
    assert llm.calls[0]["max_tokens"] == 16000


def test_composer_honors_context_model_override():
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ComposerAgent(llm=llm).run(
        _product(), {**_upstream_ctx(), "composer_model": "gpt-5-mini"}
    )
    assert llm.calls[0]["model"] == "gpt-5-mini"


def test_composer_feeds_all_upstream_findings_into_prompt():
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    user = llm.calls[0]["user"]
    assert "taxonomy" in user
    assert "parsed" in user
    assert "specialist" in user
    assert "soft_tagger" in user


def test_composer_prompt_lists_available_findings():
    """Review fix #8: composer must know which strategies actually ran so it
    can't invent a source_strategy for a skipped one."""
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    # Skip scraper — composer must not see scraper_v1 in available_findings.
    ctx = _upstream_ctx()
    del ctx["scraped"]
    ComposerAgent(llm=llm).run(_product(), ctx)
    user = llm.calls[0]["user"]
    assert '"available_findings"' in user
    assert "parser_v1" in user
    assert "scraper_v1" not in user


def test_composer_prompt_includes_validator_notes():
    """Review fix #3: validator rejections reach composer as _validator_notes
    so it can reason about dropped findings rather than treating them as
    'not asked to run'."""
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ctx = _upstream_ctx()
    ctx["_validator_notes"] = [
        {"strategy": "parser_v1", "failure_mode": "validator_rejected",
         "reasons": ["out_of_bounds:ram_gb=9999"], "error": None},
    ]
    ComposerAgent(llm=llm).run(_product(), ctx)
    assert "validator_notes" in llm.calls[0]["user"]
    assert "out_of_bounds" in llm.calls[0]["user"]


def test_composer_coerces_malformed_llm_output():
    llm = _FakeLLM({"composed_fields": "not-a-dict", "composer_decisions": "not-a-list"})
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    assert result.success is True
    assert result.output.attributes["composed_fields"] == {}
    assert result.output.attributes["composer_decisions"] == []


def test_composer_drops_decisions_that_lie_about_their_key():
    """Review fix #7: a decision whose chosen_value != composed_fields[key]
    is the LLM lying about its own choice; drop it from the audit log.
    The reconciler (#88) then synthesizes a replacement for the dropped key
    so 1:1 is still satisfied."""
    llm = _FakeLLM(
        {
            "composed_fields": {"ram_gb": 16, "storage_gb": 512},
            "composer_decisions": [
                {
                    "key": "ram_gb",
                    "chosen_value": 32,  # LIE: composed says 16
                    "source_strategy": "parser_v1",
                    "reason": "grounded",
                    "dropped_alternatives": [],
                },
                {
                    "key": "storage_gb",
                    "chosen_value": 512,  # matches
                    "source_strategy": "parser_v1",
                    "reason": "grounded",
                    "dropped_alternatives": [],
                },
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    decisions = result.output.attributes["composer_decisions"]
    # 1:1 is enforced: both keys are represented (ram_gb via synthesis).
    assert {d["key"] for d in decisions} == {"ram_gb", "storage_gb"}
    # The honest storage_gb decision is preserved as-is.
    storage_d = next(d for d in decisions if d["key"] == "storage_gb")
    assert storage_d["reason"] == "grounded"
    # The lying ram_gb decision was dropped; the synthesized replacement
    # carries the reconciler's reason.
    ram_d = next(d for d in decisions if d["key"] == "ram_gb")
    assert ram_d["reason"] == "decision_synthesized_from_composed_fields"
    # incomplete_decisions flag is set because synthesis occurred.
    assert result.output.attributes.get("incomplete_decisions") is True


def test_composer_drops_decisions_with_unknown_source_strategy():
    """Review fix #7 + #8: a decision citing a source outside the known
    strategy set can't be rendered as cell lineage — the cross-check drops it.
    The reconciler (#88) then synthesizes a replacement so 1:1 is satisfied."""
    llm = _FakeLLM(
        {
            "composed_fields": {"ram_gb": 16},
            "composer_decisions": [
                {
                    "key": "ram_gb",
                    "chosen_value": 16,
                    "source_strategy": "magical_oracle_v999",
                    "reason": "grounded",
                    "dropped_alternatives": [],
                }
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    decisions = result.output.attributes["composer_decisions"]
    # The unknown-source decision was dropped; a synthesized replacement exists.
    assert len(decisions) == 1
    assert decisions[0]["key"] == "ram_gb"
    assert decisions[0]["reason"] == "decision_synthesized_from_composed_fields"
    assert result.output.attributes.get("incomplete_decisions") is True


def test_composer_deterministic_fallback_when_llm_fails():
    """Review fix #5: a transient LLM error must not leave the product
    without a canonical row. Fall back to findings-based synthesis."""
    llm = _FakeLLM(raises=RuntimeError("network down"))
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    assert result.success is True
    assert result.output.notes == "deterministic_fallback"
    composed = result.output.attributes["composed_fields"]
    # parser specs flattened in
    assert composed["ram_gb"] == 16
    assert composed["storage_gb"] == 512
    # scraper spec flattened in (scraper overrides parser on collisions —
    # weight_kg only comes from scraper here so we just check presence)
    assert composed["weight_kg"] == 1.12
    # taxonomy product_type lifted
    assert composed["product_type"] == "laptop"
    # soft_tag lifted
    assert composed["good_for_business"] == 0.9
    # use_case_fit (structured specialist output) lifted
    assert composed["specialist_use_case_fit"] == {"business": 0.9}
    # Narrative specialist keys NOT lifted.
    assert "specialist_capabilities" not in composed
    assert "specialist_audience" not in composed
    assert "specialist_buyer_questions" not in composed
    # Decisions annotate every key with a fallback reason.
    assert all(
        d["reason"].startswith("fallback_") for d in result.output.attributes["composer_decisions"]
    )


def test_composer_registered_with_disjoint_keys():
    assert "composer_v1" in registry.all_known_keys()
    keys = registry.output_keys("composer_v1")
    # incomplete_decisions added in #88 to surface reconciler synthesis gaps.
    assert keys == frozenset(
        {"composed_fields", "composer_decisions", "composed_at", "incomplete_decisions"}
    )


def test_specialist_narrative_keys_registered_via_registry():
    """The registry surfaces every agent's self-declared NARRATIVE_KEYS."""
    narrative = registry.narrative_keys()
    assert "specialist_capabilities" in narrative
    assert "specialist_audience" in narrative
    assert "specialist_buyer_questions" in narrative
    # Structured output is NOT narrative.
    assert "specialist_use_case_fit" not in narrative


# ---------------------------------------------------------------------------
# Issue #88 — 1:1 enforcement + source_kind provenance tests
# All tests below use synthetic fixtures only; live mocklaptops validation is
# deferred pending task #12 (composer uniformly empty on 200 products).
# ---------------------------------------------------------------------------


def test_composer_output_has_1_to_1_keys_happy_path():
    """All composed_fields keys have matching decisions after reconciliation."""
    llm = _FakeLLM(
        {
            "composed_fields": {"ram_gb": 16, "product_type": "laptop", "weight_kg": 1.12},
            "composer_decisions": [
                {
                    "key": "ram_gb",
                    "chosen_value": 16,
                    "source_strategy": "parser_v1",
                    "reason": "grounded in title",
                    "dropped_alternatives": [],
                },
                {
                    "key": "product_type",
                    "chosen_value": "laptop",
                    "source_strategy": "taxonomy_v1",
                    "reason": "canonical classifier",
                    "dropped_alternatives": [],
                },
                {
                    "key": "weight_kg",
                    "chosen_value": 1.12,
                    "source_strategy": "scraper_v1",
                    "reason": "manufacturer page",
                    "dropped_alternatives": [],
                },
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    attrs = result.output.attributes
    composed_keys = set(attrs["composed_fields"].keys())
    decision_keys = {d["key"] for d in attrs["composer_decisions"]}
    assert composed_keys == decision_keys


def test_composer_synthesizes_decision_for_orphan_composed_key():
    """When LLM emits a composed key without a matching decision, the
    reconciler synthesizes one marked source_kind=PARAMETRIC with the
    canonical reason string, and sets incomplete_decisions=True."""
    llm = _FakeLLM(
        {
            "composed_fields": {"cpu": "Intel i7", "ram_gb": 16},
            "composer_decisions": [
                # ram_gb has a decision; cpu does NOT
                {
                    "key": "ram_gb",
                    "chosen_value": 16,
                    "source_strategy": "parser_v1",
                    "reason": "grounded",
                    "dropped_alternatives": [],
                }
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    attrs = result.output.attributes

    # 1:1 is satisfied after reconciliation
    composed_keys = set(attrs["composed_fields"].keys())
    decision_keys = {d["key"] for d in attrs["composer_decisions"]}
    assert composed_keys == decision_keys

    # Synthesized decision for the orphan key
    cpu_decision = next(d for d in attrs["composer_decisions"] if d["key"] == "cpu")
    assert cpu_decision["reason"] == "decision_synthesized_from_composed_fields"
    assert cpu_decision["source_kind"] == SourceKind.PARAMETRIC.value

    # Flag is set so the inspector can surface the gap
    assert attrs.get("incomplete_decisions") is True


def test_source_kind_inference_from_source_strategy():
    """For every entry in _STRATEGY_TO_SOURCE_KIND, the reconciler assigns
    the correct SourceKind to an existing decision."""
    for strategy, expected_kind in _STRATEGY_TO_SOURCE_KIND.items():
        composed = {"some_key": "some_value"}
        decisions = [
            {
                "key": "some_key",
                "chosen_value": "some_value",
                "source_strategy": strategy,
                "reason": "test",
                "dropped_alternatives": [],
            }
        ]
        reconciled, _ = _reconcile_composer_output(composed, decisions)
        assert len(reconciled) == 1
        assert reconciled[0]["source_kind"] == expected_kind.value, (
            f"strategy={strategy!r}: expected {expected_kind.value!r}, "
            f"got {reconciled[0]['source_kind']!r}"
        )


def test_unknown_source_strategy_defaults_to_parametric(caplog):
    """An unrecognised source_strategy falls back to PARAMETRIC and emits
    a warning log so the gap is surfaceable in tracing."""
    import logging

    composed = {"some_key": "some_value"}
    decisions = [
        {
            "key": "some_key",
            "chosen_value": "some_value",
            "source_strategy": "future_agent_v1",
            "reason": "test",
            "dropped_alternatives": [],
        }
    ]
    with caplog.at_level(logging.WARNING, logger="app.enrichment.agents.composer"):
        reconciled, _ = _reconcile_composer_output(composed, decisions)

    assert reconciled[0]["source_kind"] == SourceKind.PARAMETRIC.value
    # The warning message includes the unknown strategy name either in the
    # formatted message or in the args tuple (depending on log handler).
    warning_texts = " ".join(
        record.getMessage() for record in caplog.records
        if record.levelname == "WARNING"
    )
    assert "future_agent_v1" in warning_texts, (
        f"Expected warning mentioning 'future_agent_v1', got: {warning_texts!r}"
    )


def test_reconcile_does_not_set_incomplete_decisions_when_all_present():
    """When all composed keys have decisions, incomplete_decisions must NOT
    be set (no false positives in the inspector)."""
    composed = {"a": 1, "b": 2}
    decisions = [
        {"key": "a", "chosen_value": 1, "source_strategy": "parser_v1",
         "reason": "r", "dropped_alternatives": []},
        {"key": "b", "chosen_value": 2, "source_strategy": "taxonomy_v1",
         "reason": "r", "dropped_alternatives": []},
    ]
    reconciled, notes = _reconcile_composer_output(composed, decisions)
    assert "incomplete_decisions" not in notes
    assert {d["key"] for d in reconciled} == {"a", "b"}


def test_source_kind_set_on_decisions_via_full_agent_run():
    """End-to-end: source_kind appears on every decision in the agent output."""
    llm = _FakeLLM(
        {
            "composed_fields": {"ram_gb": 16, "product_type": "laptop"},
            "composer_decisions": [
                {
                    "key": "ram_gb",
                    "chosen_value": 16,
                    "source_strategy": "parser_v1",
                    "reason": "grounded",
                    "dropped_alternatives": [],
                },
                {
                    "key": "product_type",
                    "chosen_value": "laptop",
                    "source_strategy": "taxonomy_v1",
                    "reason": "canonical",
                    "dropped_alternatives": [],
                },
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    decisions = result.output.attributes["composer_decisions"]
    assert all("source_kind" in d for d in decisions)
    ram_d = next(d for d in decisions if d["key"] == "ram_gb")
    pt_d = next(d for d in decisions if d["key"] == "product_type")
    assert ram_d["source_kind"] == SourceKind.RAW_PARSE.value
    assert pt_d["source_kind"] == SourceKind.PARAMETRIC.value


# ---------------------------------------------------------------------------
# Scraper citation passthrough (issue #118)
# ---------------------------------------------------------------------------


def _ctx_with_cited_scraped():
    ctx = _upstream_ctx()
    # scraper_v1 (scraper-v2) emits cited dicts per field.
    ctx["scraped"] = {
        "scraped_specs": {
            "weight_kg": {
                "value": 1.12,
                "source_url": "https://lenovo.com/x1",
                "source_domain": "lenovo.com",
                "snippet": "Weight: 1.12 kg",
                "extracted_at": "2026-04-23T00:00:00+00:00",
            }
        }
    }
    return ctx


def test_composer_attaches_scraped_citation_to_decision():
    """When scraper_v1 fills a field with cited data, the composer stamps
    ``decision['source']`` with {kind:'scraped', url, domain, snippet}."""
    llm = _FakeLLM(
        {
            "composed_fields": {"weight_kg": 1.12},
            "composer_decisions": [
                {
                    "key": "weight_kg",
                    "chosen_value": 1.12,
                    "source_strategy": "scraper_v1",
                    "reason": "manufacturer page",
                    "dropped_alternatives": [],
                }
            ],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _ctx_with_cited_scraped())
    decisions = result.output.attributes["composer_decisions"]
    weight_d = next(d for d in decisions if d["key"] == "weight_kg")
    assert weight_d["source"] == {
        "kind": "scraped",
        "url": "https://lenovo.com/x1",
        "domain": "lenovo.com",
        "snippet": "Weight: 1.12 kg",
    }
    # composed_fields got the unwrapped scalar, not the citation dict.
    assert result.output.attributes["composed_fields"]["weight_kg"] == 1.12


def test_composer_fallback_attaches_scraped_citation():
    """Deterministic fallback path (LLM failed): scraper citations still
    flow through so the inspector has a trail even on LLM blips."""
    llm = _FakeLLM(payload=None, raises=RuntimeError("boom"))
    result = ComposerAgent(llm=llm).run(_product(), _ctx_with_cited_scraped())
    assert result.output.notes == "deterministic_fallback"
    decisions = result.output.attributes["composer_decisions"]
    weight_d = next((d for d in decisions if d["key"] == "weight_kg"), None)
    assert weight_d is not None
    assert weight_d["source"] == {
        "kind": "scraped",
        "url": "https://lenovo.com/x1",
        "domain": "lenovo.com",
        "snippet": "Weight: 1.12 kg",
    }


def test_composer_synthesized_decision_gets_scraper_citation():
    """If LLM composes weight_kg but forgets the decision, the reconciler
    synthesizes one. That synthesized decision should still pick up the
    scraper citation (so inspector trail never misses the URL)."""
    llm = _FakeLLM(
        {
            "composed_fields": {"weight_kg": 1.12},
            # LLM omits the weight_kg decision entirely.
            "composer_decisions": [],
        }
    )
    result = ComposerAgent(llm=llm).run(_product(), _ctx_with_cited_scraped())
    decisions = result.output.attributes["composer_decisions"]
    weight_d = next(d for d in decisions if d["key"] == "weight_kg")
    # Upgraded from synthesized → scraper_v1.
    assert weight_d["source_strategy"] == "scraper_v1"
    assert weight_d["source_kind"] == SourceKind.SCRAPE.value
    assert weight_d["source"]["url"] == "https://lenovo.com/x1"
