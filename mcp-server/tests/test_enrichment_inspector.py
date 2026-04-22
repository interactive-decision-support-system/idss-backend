"""Unit tests for ``scripts/enrichment_inspector.py`` pure functions.

The inspector is a Streamlit script and most of its surface is rendering,
but ``kg_property_catalog()`` is a plain function that composes the KG
property reference table the dashboard renders. We test it directly so
that if ``kg_projection.TAG_CONFIDENCE_THRESHOLD`` (#60) ever moves, the
inspector's user-visible note moves with it instead of silently lying.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Load the script as a module. It lives under scripts/, not in the app
# package, so a normal import won't find it. We do this once per session.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "enrichment_inspector.py"


@pytest.fixture(scope="module")
def inspector_module():
    if not _SCRIPT_PATH.exists():
        pytest.skip(f"inspector script not found at {_SCRIPT_PATH}")

    spec = importlib.util.spec_from_file_location(
        "enrichment_inspector_under_test", _SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        # The script transitively pulls in the full mcp-server import chain
        # (streamlit, sqlalchemy, redis via app.endpoints, …). Skip when any
        # of those are missing instead of failing — this test only cares
        # about kg_property_catalog(), which is pure.
        pytest.skip(f"inspector script can't be imported in this env: {exc}")
    return module


def test_kg_property_catalog_soft_tagger_row_carries_live_threshold(
    inspector_module,
):
    """If the projection's TAG_CONFIDENCE_THRESHOLD changes, the inspector
    note must change with it — no stale 0.5 baked into the dashboard."""
    from app import kg_projection

    rows = inspector_module.kg_property_catalog()
    soft_rows = [r for r in rows if r["producer"] == "pattern:soft_tagger_v1"]
    assert soft_rows, "expected at least one soft_tagger_v1 KEY_PATTERNS row"

    expected_threshold = float(kg_projection.TAG_CONFIDENCE_THRESHOLD)
    for row in soft_rows:
        note = row["notes"]
        assert "Cypher" in note and "#60" in note, (
            f"soft-tagger note should anchor to #60: {note!r}"
        )
        assert str(expected_threshold) in note, (
            "soft-tagger note must carry the live "
            f"TAG_CONFIDENCE_THRESHOLD ({expected_threshold}); got: {note!r}"
        )


def test_kg_property_catalog_only_soft_tagger_rows_get_threshold_note(
    inspector_module,
):
    """Identity / flattening / reserved rows must not inherit the soft-tag
    note — that gating is the whole point of the producer-strategy check."""
    rows = inspector_module.kg_property_catalog()
    for row in rows:
        if row["producer"] == "pattern:soft_tagger_v1":
            continue
        assert row["notes"] == "", (
            f"non-soft-tagger row leaked the threshold note: {row!r}"
        )


# ---------------------------------------------------------------------------
# Cell-lineage helpers (PR #86) — pure functions behind the side panel.
# We test the indexer + helpers directly so the #5 "manual sync" drift
# concern from the review becomes a build-time failure: if someone adds
# a new strategy to composer._SHORT_TO_STRATEGY, the inspector's derived
# set grows automatically and the assertion here passes; if the import
# chain breaks, the fallback tuple is caught by a separate test.
# ---------------------------------------------------------------------------


def _write_jsonl(tmp_path, records):
    path = tmp_path / f"{uuid4().hex}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return str(path)


def _mtime(path: str) -> float:
    return os.stat(path).st_mtime


def _strategy_span(strategy: str, pid: str, *, start: float, end: float,
                   attributes: dict):
    return {
        "name": strategy,
        "input": {"product_id": pid},
        "updates": [{"output": {"attributes": attributes, "notes": None},
                     "metadata": {}}],
        "started_at": start,
        "ended_at": end,
    }


def _llm_span(model: str, *, start: float, end: float, pid: str | None = None):
    inp: dict = {"system": "s", "user": "u"}
    if pid is not None:
        inp["product_id"] = pid
    return {
        "name": f"llm:{model}",
        "input": inp,
        "updates": [{"output": {"text": "ok"}, "metadata": {"input_tokens": 1,
                                                             "output_tokens": 1}}],
        "started_at": start,
        "ended_at": end,
    }


def test_known_upstream_strategies_matches_composer_short_to_strategy(
    inspector_module,
):
    """Review fix #5: the inspector's known-source set must be sourced
    from composer._SHORT_TO_STRATEGY, not a hand-maintained list.

    A future agent landing in _SHORT_TO_STRATEGY (e.g. a new retriever
    strategy) must automatically be recognizable by the side panel —
    otherwise finding-cell routing and dashed-edge inference silently
    skip it. This assertion flips the silent drift into a test failure."""
    from app.enrichment.agents.composer import _SHORT_TO_STRATEGY

    assert set(inspector_module._KNOWN_UPSTREAM_STRATEGIES) == set(
        _SHORT_TO_STRATEGY.values()
    )


def test_index_trace_attributes_llm_spans_by_time_window(
    inspector_module, tmp_path
):
    """Review fixes #2 and #3: LLM spans must be attributed to their
    owning strategy by time-interval containment, not by "most recently
    seen strategy". This test uses the JSONL write order that the real
    tracer produces (child LLM span flushes before its parent strategy
    span, because ``__exit__`` runs inner-to-outer) — the "most recently
    seen" approach would attribute every LLM to whatever strategy
    flushed immediately after it, which is the NEXT strategy, not the
    OWNING one. Time-window containment has no such fragility."""
    pid = str(uuid4())
    # Ordered as the JSONL tracer actually writes it.
    records = [
        _llm_span("gpt-4o-mini", start=100.1, end=101.9),
        _strategy_span(
            "parser_v1", pid, start=100.0, end=102.0,
            attributes={"parsed_specs": {"ram_gb": 16}},
        ),
        _llm_span("gpt-4o", start=103.1, end=104.9),
        _strategy_span(
            "taxonomy_v1", pid, start=103.0, end=105.0,
            attributes={"product_type": "laptop"},
        ),
    ]
    path = _write_jsonl(tmp_path, records)
    index = inspector_module._index_trace_by_product(path, _mtime(path))
    assert pid in index
    bucket = index[pid]
    # Exactly one LLM per strategy, attributed correctly.
    llm = bucket["llm_by_strategy"]
    assert len(llm.get("parser_v1", [])) == 1
    assert len(llm.get("taxonomy_v1", [])) == 1
    # No "(unassigned)" bucket — the old fallback path should never fire.
    assert "(unassigned)" not in llm
    # The parser LLM is the gpt-4o-mini one, not the gpt-4o one.
    assert llm["parser_v1"][0]["name"] == "llm:gpt-4o-mini"
    assert llm["taxonomy_v1"][0]["name"] == "llm:gpt-4o"


def test_index_trace_drops_llm_spans_with_no_enclosing_strategy(
    inspector_module, tmp_path
):
    """An orphan LLM span (no strategy span's time window contains it)
    has no valid owner, so it simply doesn't land in any bucket — the
    alternative ("(unassigned)" bucket) was the old fragile fallback
    and we deliberately removed it."""
    pid = str(uuid4())
    records = [
        _llm_span("gpt-4o-mini", start=50.0, end=51.0),  # before any strategy
        _strategy_span("parser_v1", pid, start=100.0, end=102.0,
                       attributes={"parsed_specs": {"ram_gb": 16}}),
    ]
    path = _write_jsonl(tmp_path, records)
    index = inspector_module._index_trace_by_product(path, _mtime(path))
    bucket = index[pid]
    assert bucket["llm_by_strategy"] == {}


def test_index_trace_picks_tightest_enclosing_strategy(
    inspector_module, tmp_path
):
    """If two strategies' time windows overlap (shouldn't happen in
    production, but defensive against future parallel execution), the
    LLM is attributed to the tighter one — that's the span it was
    structurally nested in, which is the closest thing we have to parent
    information without a parent_span_id field."""
    pid = str(uuid4())
    records = [
        _strategy_span("parser_v1", pid, start=100.0, end=200.0,
                       attributes={"parsed_specs": {}}),
        _strategy_span("scraper_v1", pid, start=110.0, end=120.0,
                       attributes={"scraped_specs": {}}),
        _llm_span("gpt-4o-mini", start=112.0, end=118.0),  # inside scraper
    ]
    path = _write_jsonl(tmp_path, records)
    index = inspector_module._index_trace_by_product(path, _mtime(path))
    bucket = index[pid]
    assert len(bucket["llm_by_strategy"].get("scraper_v1", [])) == 1
    assert "parser_v1" not in bucket["llm_by_strategy"]


def test_composer_decisions_from_span_round_trip(inspector_module):
    """_composer_decisions_from_span is the only path by which
    decisions reach the side panel. It has to handle both the nested
    structure (span.updates[0].output.attributes.composer_decisions) and
    the shapes that _span_output traverses."""
    pid = str(uuid4())
    span = _strategy_span(
        "composer_v1", pid, start=1.0, end=2.0,
        attributes={
            "composed_fields": {"ram_gb": 16},
            "composer_decisions": [
                {"key": "ram_gb", "chosen_value": 16,
                 "source_strategy": "parser_v1",
                 "reason": "grounded", "dropped_alternatives": []}
            ],
            "composed_at": "x",
        },
    )
    decisions = inspector_module._composer_decisions_from_span(span)
    assert len(decisions) == 1
    assert decisions[0]["key"] == "ram_gb"
    assert decisions[0]["source_strategy"] == "parser_v1"


def test_composer_decisions_from_span_returns_empty_on_malformed(
    inspector_module,
):
    """Any shape that isn't ``attributes.composer_decisions = [dict, ...]``
    yields an empty list — the side panel renders a "no decision"
    caption rather than crashing."""
    # No updates → empty
    assert inspector_module._composer_decisions_from_span(
        {"name": "composer_v1", "input": {}}
    ) == []
    # Non-list decisions → empty
    bad = {
        "name": "composer_v1",
        "input": {"product_id": "p"},
        "updates": [{"output": {"attributes":
            {"composer_decisions": "not-a-list"}}, "metadata": {}}],
    }
    assert inspector_module._composer_decisions_from_span(bad) == []


def test_dashed_sources_for_decision_matches_upstream_value(
    inspector_module,
):
    """_dashed_sources_for_decision walks each upstream strategy's
    attributes and matches dropped_alternatives values against their
    flattened output. parser_v1 emitted ram_gb=8 which matches the
    decision's dropped_alternatives=[8], so parser_v1 should be dashed."""
    bucket = {
        "strategy_spans": {
            "parser_v1": _strategy_span(
                "parser_v1", "p", start=1, end=2,
                attributes={"parsed_specs": {"ram_gb": 8}},
            ),
            "scraper_v1": _strategy_span(
                "scraper_v1", "p", start=3, end=4,
                attributes={"scraped_specs": {"ram_gb": 16}},
            ),
        }
    }
    # Composer chose 16 (scraper), dropped 8 (parser's value).
    dashed = inspector_module._dashed_sources_for_decision(bucket, [8])
    assert dashed == ("parser_v1",)


def test_dashed_sources_for_decision_empty_when_no_dropped_alts(
    inspector_module,
):
    assert inspector_module._dashed_sources_for_decision({}, []) == ()


def test_dashed_sources_for_decision_survives_typeerror_on_compare(
    inspector_module,
):
    """Review fix #4: `in` uses `==`, and `==` against certain
    adversarial or mixed scalar types can raise TypeError. The helper
    must catch that rather than crash the whole side panel."""
    class Uncomparable:
        def __eq__(self, other):
            if isinstance(other, (int, float)):
                raise TypeError("uncomparable against number")
            return NotImplemented

        def __hash__(self) -> int:
            return 0

    bucket = {
        "strategy_spans": {
            "parser_v1": _strategy_span(
                "parser_v1", "p", start=1, end=2,
                attributes={"parsed_specs": {"ram_gb": 16}},
            )
        }
    }
    result = inspector_module._dashed_sources_for_decision(
        bucket, [Uncomparable(), 16]
    )
    # The Uncomparable comparison raises TypeError → swallowed; the 16
    # comparison still lands → parser_v1 is dashed.
    assert result == ("parser_v1",)


def test_short_pid(inspector_module):
    """Review fix #8: don't cosmetically truncate ids that are already
    short (numeric SKUs, hand-coded fixtures, etc)."""
    assert inspector_module._short_pid("abc123") == "abc123"
    assert inspector_module._short_pid("0123456789ab") == "0123456789ab"
    assert inspector_module._short_pid(
        "11111111-1111-1111-1111-111111111111"
    ) == "11111111\u2026"


def test_run_id_regex_rejects_path_traversal(inspector_module):
    """Review security note: run_id is user-adjacent through the
    artifact JSON and ends up in a filesystem path, so enforce the
    UUID-hex shape the orchestrator actually generates before trusting it."""
    safe = "48bbf00b763d49918f5fb265d4e763ca"
    assert inspector_module._RUN_ID_SAFE_RE.match(safe)
    for evil in ("../../etc/passwd", "run/../other", "48BBF00B" * 4,
                 "48bbf00b" * 3, "", "48bbf00b763d49918f5fb265d4e763ca.x"):
        assert not inspector_module._RUN_ID_SAFE_RE.match(evil), (
            f"regex accepted unsafe run_id: {evil!r}"
        )


def test_index_trace_constrains_llm_attribution_by_pid_when_present(
    inspector_module, tmp_path
):
    """Review feedback #1: when an LLM span carries a product_id (via
    input.product_id or a product:<pid> tag), the indexer must require
    the enclosing strategy to share it — otherwise parallel execution
    would let an LLM call leak into another product's bucket.

    Two products' strategies run with overlapping time windows; each
    product's LLM span falls inside *both* intervals. Without the
    pid constraint the tighter-window match can pick the wrong
    product; with it, pid wins regardless of window tightness."""
    pid_a = "11111111-1111-1111-1111-111111111111"
    pid_b = "22222222-2222-2222-2222-222222222222"
    records = [
        # Product A's strategy: wide window.
        _strategy_span("parser_v1", pid_a, start=100.0, end=200.0,
                       attributes={"parsed_specs": {}}),
        # Product B's strategy: tighter window — without pid constraint,
        # it would capture pid_a's LLM by time-window tightness alone.
        _strategy_span("parser_v1", pid_b, start=120.0, end=160.0,
                       attributes={"parsed_specs": {}}),
        # LLM from product A, tagged with A's pid, inside both windows.
        _llm_span("gpt-4o-mini", start=130.0, end=140.0, pid=pid_a),
        # LLM from product B, tagged with B's pid, also inside both.
        _llm_span("gpt-4o-mini", start=135.0, end=145.0, pid=pid_b),
    ]
    path = _write_jsonl(tmp_path, records)
    index = inspector_module._index_trace_by_product(path, _mtime(path))
    # Each product got exactly its own LLM, not the other's.
    assert len(index[pid_a]["llm_by_strategy"].get("parser_v1", [])) == 1
    assert len(index[pid_b]["llm_by_strategy"].get("parser_v1", [])) == 1
    a_llm = index[pid_a]["llm_by_strategy"]["parser_v1"][0]
    b_llm = index[pid_b]["llm_by_strategy"]["parser_v1"][0]
    assert a_llm["input"]["product_id"] == pid_a
    assert b_llm["input"]["product_id"] == pid_b


def test_index_trace_falls_back_to_time_window_when_llm_has_no_pid(
    inspector_module, tmp_path
):
    """The pid constraint kicks in *only when* the LLM span carries a
    pid. Today's tracer typically doesn't tag LLM spans with product_id,
    so the pid-less case must still match by time window alone —
    otherwise every LLM call in the existing pipeline would suddenly
    become orphaned."""
    pid = str(uuid4())
    records = [
        _strategy_span("parser_v1", pid, start=100.0, end=102.0,
                       attributes={"parsed_specs": {}}),
        _llm_span("gpt-4o-mini", start=100.5, end=101.5),  # no pid
    ]
    path = _write_jsonl(tmp_path, records)
    index = inspector_module._index_trace_by_product(path, _mtime(path))
    assert len(index[pid]["llm_by_strategy"].get("parser_v1", [])) == 1


def test_load_trace_bucket_flags_pid_not_in_run(
    inspector_module, tmp_path, monkeypatch
):
    """Review feedback #3: when the JSONL exists but the pid simply
    isn't in this run, ``_load_trace_bucket`` must flag the returned
    bucket with ``_pid_not_in_run`` so the side panel can tell the user
    "pick a newer run" rather than "composer didn't run"."""
    run_id = "e4c283a7d97b4e4a8def71d0f9d98d91"  # 32 hex chars, regex-safe
    run_pid = str(uuid4())
    missing_pid = str(uuid4())
    records = [
        _strategy_span("parser_v1", run_pid, start=1.0, end=2.0,
                       attributes={"parsed_specs": {}}),
    ]
    # Emulate the inspector's on-disk layout: TRACES_DIR / "{run_id}.jsonl".
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    jsonl_path = traces_dir / f"{run_id}.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    monkeypatch.setattr(inspector_module, "TRACES_DIR", traces_dir)
    # Clear the cache so _index_trace_by_product re-reads from disk.
    inspector_module._index_trace_by_product.clear()

    run = {"summary": {"run_id": run_id}}
    known = inspector_module._load_trace_bucket(run, run_pid)
    assert known is not None
    assert not known.get("_pid_not_in_run"), (
        "known pid should not be flagged as missing from run"
    )
    assert "parser_v1" in known["strategy_spans"]

    unknown = inspector_module._load_trace_bucket(run, missing_pid)
    assert unknown is not None
    assert unknown.get("_pid_not_in_run") is True, (
        "unknown pid should be flagged so the side panel can show the "
        "'pick a newer run' message instead of 'composer didn't run'"
    )
    assert unknown["composer"] is None
    assert unknown["strategy_spans"] == {}


def test_composer_notes_preserves_explicit_empty_string(inspector_module):
    """Review nit #2: ``_composer_notes`` must compare with ``is None``
    rather than truthiness, so a future marker like ``notes=''`` (or any
    other explicitly empty string from the composer) round-trips rather
    than being silently swapped for ``None``. Downstream callouts still
    filter empty — but new markers land here verbatim."""
    span = {
        "name": "composer_v1",
        "input": {"product_id": "p"},
        "updates": [{"output": {"notes": ""}, "metadata": {}}],
    }
    assert inspector_module._composer_notes(span) == ""
    span_none = {
        "name": "composer_v1",
        "input": {"product_id": "p"},
        "updates": [{"output": {"notes": None}, "metadata": {}}],
    }
    assert inspector_module._composer_notes(span_none) is None
