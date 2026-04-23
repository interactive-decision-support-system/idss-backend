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
    # 5 strategies × 3 products = 15 outputs. soft_tagger_v1 is default-off
    # since #115 pt 2; composer_v1 runs last as the single writer of the
    # canonical row — #83.
    expected_strategies = {
        "taxonomy_v1",
        "parser_v1",
        "specialist_v1",
        "scraper_v1",
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
    # Each successful agent contributes substantive (non-empty) values only.
    # taxonomy(2) + parser(3) + specialist(4) + scraper(2)
    # + composer(4: composed_fields, composer_decisions, composed_at, incomplete_decisions) = 15.
    # soft_tagger_v1 is default-off since #115 pt 2 (PR #121) — was previously 2.
    # taxonomy drops from 3 → 2 after the taxonomy_path cull (#115 pt 4 / this PR):
    # product_type + product_type_confidence remain.
    # scraper drops from 6 → 2 because scraped_specs={}, scraped_reviews=[],
    # scraped_qna=[], scraped_sources=[] are all empty containers and no longer
    # count — the fixture products have no URL, so the scraper produces nothing
    # useful for those four keys. scraped_at and scraped_category remain.
    # composer gains incomplete_decisions=True (PR #97: 1:1 reconciler synthesizes
    # decisions for storage_gb and product_type which the scripted LLM omitted).
    assert avg == 15.0


def test_avg_keys_filled_treats_empty_container_as_unfilled(monkeypatch):
    """Regression guard: empty container must contribute 0, not 1, to keys_filled.

    Reproduces the mocklaptops audit bug where parsed_specs={} was counted as
    one filled key, making a zero-signal run report avg_keys_filled=21.0.
    """
    from app.enrichment.orchestration.runner import _is_substantive

    # Empty containers are not substantive.
    assert _is_substantive({}) is False
    assert _is_substantive([]) is False
    assert _is_substantive("") is False
    assert _is_substantive(None) is False

    # Non-empty containers and scalars are substantive.
    assert _is_substantive({"ram_gb": 16}) is True
    assert _is_substantive([1, 2]) is True
    assert _is_substantive("laptop") is True
    assert _is_substantive(0) is True      # zero int is substantive
    assert _is_substantive(False) is True  # booleans are substantive
    assert _is_substantive(0.9) is True

    # Simulate the exact audit scenario: a composer row with only empty containers.
    # Before the fix this would have contributed 1 key (the container key itself);
    # after the fix it contributes 0.
    empty_composer_attrs = {"parsed_specs": {}}
    keys_filled_empty = sum(1 for v in empty_composer_attrs.values() if _is_substantive(v))
    assert keys_filled_empty == 0, "empty container must not count as a filled key"

    # Substantive case: same key, populated value.
    populated_composer_attrs = {"parsed_specs": {"ram_gb": 16}}
    keys_filled_populated = sum(
        1 for v in populated_composer_attrs.values() if _is_substantive(v)
    )
    assert keys_filled_populated == 1, "non-empty container must count as one filled key"


def test_orchestrated_run_skips_scraper_when_no_url(patched_runtime):
    # The LLMOrchestrator should opt out of scraper_v1 since no products have URLs.
    # Our scripted LLM doesn't return a per_product plan for the orchestrator,
    # so it falls back to forced-only (taxonomy + composer). soft_tagger_v1 is
    # default-off since #115 pt 2 — not forced and not in the fallback set.
    result = runner.run_enrichment(db=None, mode="orchestrated", limit=2, dry_run=True)
    assert result.summary.strategies_invoked.get("scraper_v1", 0) == 0
    assert result.summary.strategies_invoked["taxonomy_v1"] == 2
    assert result.summary.strategies_invoked.get("soft_tagger_v1", 0) == 0


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


# ---------------------------------------------------------------------------
# missing_fields wiring (#118)
# ---------------------------------------------------------------------------


def test_compute_missing_fields_picks_laptop_gaps_unfilled_by_parser():
    """Runner computes ctx['missing_fields'] from the category's gap list
    minus what parser / raw_attributes already filled."""
    from app.enrichment.orchestration.runner import _compute_missing_fields

    p = ProductInput(
        product_id=uuid4(),
        title="ThinkPad",
        category="Electronics",
        raw_attributes={"weight_kg": 1.1},  # raw already has weight_kg
    )
    ctx = {
        "taxonomy": {"product_type": "laptop"},
        "parsed": {"parsed_specs": {"ram_gb": 16, "storage_gb": 512}},
    }
    missing = _compute_missing_fields(ctx, p)
    # Laptop gap list minus {ram_gb, storage_gb, weight_kg} = 6 fields.
    assert "ram_gb" not in missing
    assert "storage_gb" not in missing
    assert "weight_kg" not in missing
    assert "cpu_model" in missing
    assert "gpu_model" in missing


def test_compute_missing_fields_empty_for_unknown_category():
    from app.enrichment.orchestration.runner import _compute_missing_fields

    p = ProductInput(product_id=uuid4(), title="X", category="Books")
    # No taxonomy, non-laptop category → no gap list.
    ctx = {}
    assert _compute_missing_fields(ctx, p) == []
