"""composer_v1 — single writer of the canonical enriched row (issue #83)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.composer import ComposerAgent
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.types import ProductInput


@pytest.fixture(autouse=True)
def _registry_clean():
    registry._reset_for_tests()
    registry.register(ComposerAgent)
    yield
    registry._reset_for_tests()


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def complete(self, **kw):
        self.calls.append(kw)
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


def test_composer_strips_echoes_of_raw_identity_fields():
    """If the LLM echoes the raw title/brand/description/color, drop it."""
    llm = _FakeLLM(
        {
            "composed_fields": {
                "title": "ThinkPad X1 Carbon, 16GB RAM, 512GB SSD",  # echo
                "color": "black",  # echo (from raw_attributes)
                "ram_gb": 16,  # keep
            },
            "composer_decisions": [],
        }
    )
    agent = ComposerAgent(llm=llm)
    result = agent.run(_product(), _upstream_ctx())
    assert result.output.attributes["composed_fields"] == {"ram_gb": 16}


def test_composer_strips_narrative_keys():
    """Specialist narrative output must never land on the canonical row."""
    llm = _FakeLLM(
        {
            "composed_fields": {
                "specialist_capabilities": ["long battery"],
                "specialist_audience": {"professionals": "lightweight"},
                "specialist_buyer_questions": ["What's the warranty?"],
                "ram_gb": 16,
            },
            "composer_decisions": [],
        }
    )
    agent = ComposerAgent(llm=llm)
    result = agent.run(_product(), _upstream_ctx())
    assert result.output.attributes["composed_fields"] == {"ram_gb": 16}


def test_composer_skips_llm_when_no_upstream_findings():
    """An empty context means no findings to synthesize — don't waste a gpt-5 call."""
    llm = _FakeLLM({"composed_fields": {"oops": "should_not_see_this"}})
    agent = ComposerAgent(llm=llm)
    result = agent.run(_product(), {})  # no upstream findings
    assert llm.calls == []
    assert result.output.attributes["composed_fields"] == {}
    assert result.output.attributes["composer_decisions"] == []
    assert result.output.notes == "no_upstream_findings"


def test_composer_uses_json_mode_and_composer_model_default():
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    assert llm.calls[0]["json_mode"] is True
    # default composer tier is gpt-5 when no env override is set
    assert llm.calls[0]["model"] == "gpt-5"


def test_composer_honors_context_model_override():
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ComposerAgent(llm=llm).run(_product(), {**_upstream_ctx(), "composer_model": "gpt-5-mini"})
    assert llm.calls[0]["model"] == "gpt-5-mini"


def test_composer_feeds_all_upstream_findings_into_prompt():
    llm = _FakeLLM({"composed_fields": {}, "composer_decisions": []})
    ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    user = llm.calls[0]["user"]
    assert "taxonomy" in user
    assert "parsed" in user
    assert "specialist" in user
    assert "soft_tagger" in user


def test_composer_coerces_malformed_llm_output():
    """Non-dict composed_fields and non-list decisions are coerced to empties."""
    llm = _FakeLLM({"composed_fields": "not-a-dict", "composer_decisions": "not-a-list"})
    result = ComposerAgent(llm=llm).run(_product(), _upstream_ctx())
    assert result.success is True
    assert result.output.attributes["composed_fields"] == {}
    assert result.output.attributes["composer_decisions"] == []


def test_composer_registered_with_disjoint_keys():
    """OUTPUT_KEYS must be disjoint from every other registered strategy."""
    assert "composer_v1" in registry.all_known_keys()
    keys = registry.output_keys("composer_v1")
    assert keys == frozenset({"composed_fields", "composer_decisions", "composed_at"})
