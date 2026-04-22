"""composer_v1 — single writer of the canonical enriched row (issue #83)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.composer import ComposerAgent
from app.enrichment.agents.specialist import SpecialistAgent
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.types import ProductInput


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
            "taxonomy_path": ["electronics", "computers", "laptop"],
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
    assert attrs["composer_decisions"][0]["key"] == "ram_gb"
    assert attrs["composer_decisions"][0]["source_strategy"] == "parser_v1"
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
    # Task 12: raised to 6000 to satisfy gpt-5-mini reasoning-floor requirement.
    assert llm.calls[0]["max_tokens"] == 6000


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
    is the LLM lying about its own choice; drop it from the audit log."""
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
    assert len(decisions) == 1
    assert decisions[0]["key"] == "storage_gb"


def test_composer_drops_decisions_with_unknown_source_strategy():
    """Review fix #7 + #8: a decision citing a source outside the known
    strategy set can't be rendered as cell lineage — drop it."""
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
    assert result.output.attributes["composer_decisions"] == []


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
    assert keys == frozenset({"composed_fields", "composer_decisions", "composed_at"})


def test_specialist_narrative_keys_registered_via_registry():
    """The registry surfaces every agent's self-declared NARRATIVE_KEYS."""
    narrative = registry.narrative_keys()
    assert "specialist_capabilities" in narrative
    assert "specialist_audience" in narrative
    assert "specialist_buyer_questions" in narrative
    # Structured output is NOT narrative.
    assert "specialist_use_case_fit" not in narrative
