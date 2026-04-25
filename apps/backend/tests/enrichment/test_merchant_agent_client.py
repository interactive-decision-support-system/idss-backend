"""Phase 1 scaffold: in-process MerchantAgentClient surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.tools import merchant_agent_client as mac
from merchant_agent.enrichment.types import SlotSchema


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    registry._reset_for_tests()
    monkeypatch.setattr(mac, "_PROPOSALS_DIR", tmp_path / "proposals")
    yield
    registry._reset_for_tests()


def test_known_schema_includes_raw_columns():
    schema = mac.get_known_schema("default")
    # A few raw columns the audit confirmed exist.
    assert "title" in schema or "name" in schema  # ORM attr is "name", column is "title"
    assert "category" in schema
    assert "attributes" in schema
    # Excluded housekeeping
    assert "created_at" not in schema
    assert "updated_at" not in schema


def test_known_schema_includes_external_strategy_keys():
    schema = mac.get_known_schema("default")
    # normalizer_v1 is registered as an external strategy
    assert "normalized_description" in schema
    assert "normalizer_v1" in schema["normalized_description"].source_strategies


def test_propose_extension_marks_known_keys_accepted():
    slot = SlotSchema(key="brand", type="text")  # already a raw column
    ack = mac.propose_schema_extension("default", [slot])
    assert ack.decisions[0].decision == "accepted"


def test_propose_extension_marks_novel_keys_deferred(tmp_path):
    slot = SlotSchema(key="wattage", type="numeric", unit="W")
    ack = mac.propose_schema_extension("default", [slot])
    assert ack.decisions[0].decision == "deferred"
    # File was written
    files = list(Path(mac._PROPOSALS_DIR).glob("*.json"))
    assert len(files) == 1


def test_type_inference_detects_numeric_and_boolean():
    assert mac._infer_strategy_key_type("good_for_gaming") == "boolean"
    assert mac._infer_strategy_key_type("parsed_ram_gb") == "numeric"
    assert mac._infer_strategy_key_type("scraped_url") == "text"
