"""Phase 3: runner end-to-end with mocked LLM + DB writes.

Avoids Postgres entirely: monkey-patches load_products + db_writer + LLMClient.
Verifies plan execution order, context flow, validator gating, summary tally,
and CatalogSchema synthesis.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.composer import ComposerAgent
from app.enrichment.agents.parser import ParserAgent
from app.enrichment.agents.soft_tagger import SoftTaggerAgent
from app.enrichment.agents.specialist import SpecialistAgent
from app.enrichment.agents.taxonomy import TaxonomyAgent
from app.enrichment.agents.web_scraper import WebScraperAgent
from app.enrichment.orchestration import runner
from app.enrichment.tools import db_writer, merchant_agent_client, scraper_client
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.types import ProductInput


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    registry._reset_for_tests()
    for cls in (
        TaxonomyAgent,
        ParserAgent,
        SpecialistAgent,
        WebScraperAgent,
        SoftTaggerAgent,
        ComposerAgent,
    ):
        registry.register(cls)
    # Isolate side-effect filesystem.
    monkeypatch.setattr(merchant_agent_client, "_PROPOSALS_DIR", tmp_path / "proposals")
    monkeypatch.setattr(scraper_client, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(scraper_client, "_LOG_PATH", tmp_path / "log.jsonl")
    cfg = tmp_path / "scraper_sources.yaml"
    cfg.write_text("laptop:\n  - example-manufacturer.com\n", encoding="utf-8")
    monkeypatch.setattr(scraper_client, "_config_path", lambda: cfg)
    yield
    registry._reset_for_tests()


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Returns canned JSON per system-prompt-keyword.

    The runner instantiates each agent via registry.get(...)(); each agent
    in turn news up its own LLMClient. We monkey-patch LLMClient.complete
    here so every agent shares this scripted backend.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def complete(self, **kw):
        self.calls.append(kw)
        system = kw.get("system", "")
        if "classify e-commerce products" in system:
            payload = {
                "product_type": "laptop",
                "taxonomy_path": ["electronics", "laptop"],
                "confidence": 0.9,
            }
        elif "extract product specifications" in system:
            payload = {
                "parsed_specs": {"ram_gb": 16, "storage_gb": 512},
                "parsed_source_fields": {"ram_gb": "title", "storage_gb": "title"},
            }
        elif "domain specialist" in system:
            payload = {
                "specialist_capabilities": ["business-class build", "long battery"],
                "specialist_use_case_fit": {"business": 0.9},
                "specialist_audience": {"professionals": "lightweight"},
                "specialist_buyer_questions": ["What's the warranty?"],
            }
        elif "soft 'good_for_*' tags" in system:
            payload = {"good_for_tags": {"good_for_business": 0.9}}
        elif "discovered_product_types" in system:
            payload = {"discovered_product_types": ["laptop"]}
        elif "composer agent" in system:
            payload = {
                "composed_fields": {"ram_gb": 16, "storage_gb": 512, "product_type": "laptop"},
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
        else:
            payload = {}
        return LLMResponse(
            text="",
            model=kw.get("model") or "gpt-4o-mini",
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.00001,
            latency_ms=1,
            parsed_json=payload,
        )


def _products(n=3):
    out = []
    for i in range(n):
        out.append(
            ProductInput(
                product_id=uuid4(),
                title=f"ThinkPad X1 Carbon Gen {i+5}, 16GB RAM, 512GB SSD",
                category="Electronics",
                brand="Lenovo",
                description="Business ultraportable.",
                price=Decimal("1299.00"),
                raw_attributes={"description": "Business ultraportable."},
            )
        )
    return out


@pytest.fixture
def patched_runtime(monkeypatch):
    """Patch the runner's external dependencies in one place."""
    from app.enrichment.tools import llm_client as llm_module
    from app.enrichment.tools import catalog_reader

    products = _products(3)

    def _fake_load(*_a, **kw):
        lim = kw.get("limit")
        return products if lim is None else products[:lim]

    monkeypatch.setattr(catalog_reader, "load_products", _fake_load)
    monkeypatch.setattr(runner, "load_products", _fake_load)

    scripted = _ScriptedLLM()
    monkeypatch.setattr(llm_module.LLMClient, "complete", lambda self, **kw: scripted.complete(**kw))

    written: list = []
    monkeypatch.setattr(
        db_writer,
        "upsert_one",
        lambda db, out, *, enriched_model, dry_run=False: written.append(out),
    )
    monkeypatch.setattr(
        db_writer,
        "upsert_many",
        lambda db, outs, *, enriched_model, dry_run=False: written.extend(outs) or len(written),
    )
    return {"products": products, "scripted": scripted, "written": written}


def test_fixed_run_writes_one_row_per_strategy_per_product(patched_runtime):
    result = runner.run_enrichment(
        db=None,  # patched_runtime stubs out the DB-touching paths
        mode="fixed",
        merchant_id="default",
        limit=3,
        dry_run=False,
    )
    assert result.summary.products_processed == 3
    # 6 strategies × 3 products = 18 outputs (composer_v1 runs last as the
    # single writer of the canonical row — #83).
    expected_strategies = {
        "taxonomy_v1",
        "parser_v1",
        "specialist_v1",
        "scraper_v1",
        "soft_tagger_v1",
        "composer_v1",
    }
    assert set(result.summary.strategies_invoked.keys()) == expected_strategies
    for s in expected_strategies:
        assert result.summary.strategies_invoked[s] == 3


def test_fixed_run_passes_taxonomy_into_specialist_context(patched_runtime):
    runner.run_enrichment(db=None, mode="fixed", limit=1, dry_run=True)
    # The specialist's system prompt is loaded based on product_type read from
    # context — finding the laptop prompt fragment proves the context flowed.
    specialist_calls = [
        c for c in patched_runtime["scripted"].calls if "domain specialist" in c.get("system", "")
    ]
    assert specialist_calls, "specialist was never called"
    # laptop.md has the word 'Workload fit' as its first capability bullet.
    assert any("Workload fit" in c["system"] for c in specialist_calls)


def test_run_returns_catalog_schema_and_proposes_extension(patched_runtime):
    result = runner.run_enrichment(db=None, mode="fixed", limit=2, dry_run=True)
    schema = result.schema
    assert schema.merchant_id == "default"
    assert schema.catalog_size == 2
    type_names = {pt.product_type for pt in schema.product_types}
    assert "laptop" in type_names
    # Proposal id was recorded.
    assert result.summary.schema_proposal_id


def test_run_reports_avg_keys_filled(patched_runtime):
    result = runner.run_enrichment(db=None, mode="fixed", limit=2, dry_run=True)
    avg = result.summary.to_dict()["avg_keys_filled_per_product"]
    # Each successful agent contributes its OUTPUT_KEYS count;
    # taxonomy(3) + parser(3) + specialist(4) + scraper(6) + soft_tagger(2)
    # + composer(3: composed_fields, composer_decisions, composed_at) = 21
    assert avg == 21.0


def test_orchestrated_run_skips_scraper_when_no_url(patched_runtime):
    # The LLMOrchestrator should opt out of scraper_v1 since no products have URLs.
    # Our scripted LLM doesn't return a per_product plan for the orchestrator,
    # so it falls back to forced-only (taxonomy + soft_tagger). Verify.
    result = runner.run_enrichment(db=None, mode="orchestrated", limit=2, dry_run=True)
    assert result.summary.strategies_invoked.get("scraper_v1", 0) == 0
    assert result.summary.strategies_invoked["taxonomy_v1"] == 2
    assert result.summary.strategies_invoked["soft_tagger_v1"] == 2


def test_run_with_no_products_returns_empty_result(monkeypatch):
    from app.enrichment.tools import llm_client as llm_module

    monkeypatch.setattr(runner, "load_products", lambda *a, **kw: [])
    scripted = _ScriptedLLM()
    monkeypatch.setattr(llm_module.LLMClient, "complete", lambda self, **kw: scripted.complete(**kw))

    result = runner.run_enrichment(db=None, mode="fixed", limit=10)
    assert result.summary.products_processed == 0
    assert "no_products_loaded" in result.summary.notes
    assert result.schema.catalog_size == 0


# ---------------------------------------------------------------------------
# Regression: run_id propagation into ThreadPoolExecutor workers (#96 fix)
# ---------------------------------------------------------------------------


def test_worker_spans_land_in_run_id_jsonl_not_no_run(
    patched_runtime, monkeypatch, tmp_path
):
    """Each worker thread must inherit the run_context ContextVar so that
    _JsonlTracer writes spans to <run_id>.jsonl, not no_run.jsonl.

    Regression for the bug introduced by PR #96 (ThreadPoolExecutor):
    before the fix, all 110 worker spans landed in no_run.jsonl while the
    main-thread orchestration spans appeared in <run_id>.jsonl.
    """
    import os
    import json
    from app.enrichment import tracing as tracing_mod

    # Enable the JSONL tracer and redirect output to tmp_path.
    trace_dir = tmp_path / "traces"
    monkeypatch.setenv("ENRICHMENT_TRACE_JSONL", "1")
    monkeypatch.setattr(tracing_mod, "_TRACER", None)  # force rebuild
    monkeypatch.setattr(
        tracing_mod,
        "_TRACER",
        tracing_mod._JsonlTracer(trace_dir),
    )

    result = runner.run_enrichment(
        db=None,
        mode="fixed",
        merchant_id="default",
        limit=5,
        dry_run=True,
    )

    run_id = result.summary.run_id
    run_file = trace_dir / f"{run_id}.jsonl"
    no_run_file = trace_dir / "no_run.jsonl"

    # The no_run.jsonl file must NOT exist (or be empty) — no worker spans
    # should have fallen through without a run_id.
    assert not no_run_file.exists(), (
        f"no_run.jsonl exists with {no_run_file.stat().st_size} bytes — "
        "worker threads are not inheriting the run_context ContextVar"
    )

    # The run-tagged file must exist and contain worker spans.
    assert run_file.exists(), f"Expected trace file {run_file} was not created"
    lines = [json.loads(l) for l in run_file.read_text().splitlines() if l.strip()]
    assert lines, "run_id trace file is empty"

    # Every span must carry the correct run tag.
    expected_tag = f"run:{run_id}"
    for span in lines:
        assert expected_tag in span.get("tags", []), (
            f"span {span.get('name')} is missing tag '{expected_tag}': {span.get('tags')}"
        )
