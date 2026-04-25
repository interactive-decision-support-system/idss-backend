"""llm_client — model-tier helpers + GPT-5 family pricing (issue #83)."""

from __future__ import annotations

import pytest

from merchant_agent.enrichment.tools import llm_client


def test_default_model_defaults_to_gpt5_mini(monkeypatch):
    monkeypatch.delenv("ENRICHMENT_DEFAULT_MODEL", raising=False)
    assert llm_client.default_model() == "gpt-5-mini"


def test_default_model_large_defaults_to_gpt5(monkeypatch):
    monkeypatch.delenv("ENRICHMENT_LARGE_MODEL", raising=False)
    assert llm_client.default_model(large=True) == "gpt-5"


def test_composer_model_defaults_to_gpt5(monkeypatch):
    monkeypatch.delenv("ENRICHMENT_COMPOSER_MODEL", raising=False)
    monkeypatch.delenv("ENRICHMENT_LARGE_MODEL", raising=False)
    assert llm_client.composer_model() == "gpt-5"


def test_composer_model_falls_back_to_large_when_unset(monkeypatch):
    """If COMPOSER isn't set but LARGE is, use LARGE — cheap rollout path."""
    monkeypatch.delenv("ENRICHMENT_COMPOSER_MODEL", raising=False)
    monkeypatch.setenv("ENRICHMENT_LARGE_MODEL", "gpt-5-mini")
    assert llm_client.composer_model() == "gpt-5-mini"


def test_composer_model_env_override_wins(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_COMPOSER_MODEL", "gpt-5")
    monkeypatch.setenv("ENRICHMENT_LARGE_MODEL", "gpt-4o")
    assert llm_client.composer_model() == "gpt-5"


def test_utility_model_defaults_to_gpt5_nano(monkeypatch):
    monkeypatch.delenv("ENRICHMENT_UTILITY_MODEL", raising=False)
    assert llm_client.utility_model() == "gpt-5-nano"


@pytest.mark.parametrize(
    "model",
    ["gpt-5-nano", "gpt-5-mini", "gpt-5", "gpt-4o-mini", "gpt-4o"],
)
def test_pricing_table_has_gpt5_family(model):
    assert model in llm_client._PRICING
    input_rate, output_rate = llm_client._PRICING[model]
    assert input_rate > 0
    assert output_rate > 0


def test_gpt5_tier_pricing_monotonic():
    """gpt-5-nano < gpt-5-mini < gpt-5 on both input and output rates."""
    nano_in, nano_out = llm_client._PRICING["gpt-5-nano"]
    mini_in, mini_out = llm_client._PRICING["gpt-5-mini"]
    full_in, full_out = llm_client._PRICING["gpt-5"]
    assert nano_in < mini_in < full_in
    assert nano_out < mini_out <= full_out
