"""Regression tests for #111 — nested Langfuse tracing with trace-level tags.

Before #111 every span was a root-level ``generation()`` and tags passed to
``generation()`` were silently dropped by the Langfuse v2 SDK. These tests
exercise the fixed tracer against a mocked client and assert:

1. Exactly one ``client.trace(...)`` call per run_id, with all three tags
   (run / merchant / kg_strategy) passed at trace level.
2. Non-LLM spans call ``parent.span(...)``; names prefixed ``llm:`` call
   ``parent.generation(...)``.
3. Nested ``tracer.span(...)`` blocks parent onto the enclosing span, not
   onto the trace — so strategy spans inside a ``product:<id>`` span become
   its children (the fix's structural goal).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from merchant_agent.enrichment import tracing
from merchant_agent.enrichment.tracing import _LangfuseTracer, run_context


@pytest.fixture(autouse=True)
def _reset_tracer():
    tracing._reset_for_tests()
    yield
    tracing._reset_for_tests()


def _make_client() -> MagicMock:
    """Mock Langfuse v2 client: trace()/span()/generation() return handles
    that themselves expose span()/generation()/update()/end()."""
    client = MagicMock(name="langfuse_client")
    # trace() returns a handle with span()/generation()/update()/end()
    client.trace.return_value = MagicMock(name="trace")
    return client


def test_creates_one_trace_per_run_with_trace_level_tags():
    client = _make_client()
    tracer = _LangfuseTracer(client)

    with run_context(run_id="run-abc", merchant_id="acme", kg_strategy="default"):
        with tracer.span(name="parser_v1"):
            pass
        with tracer.span(name="specialist_v1"):
            pass

    assert client.trace.call_count == 1
    kwargs = client.trace.call_args.kwargs
    assert kwargs["id"] == "run-abc"
    assert "enrichment_run:acme:run-abc" in kwargs["name"]
    assert set(kwargs["tags"]) == {
        "run:run-abc",
        "merchant:acme",
        "kg_strategy:default",
    }


def test_non_llm_span_goes_to_parent_span_not_generation():
    client = _make_client()
    tracer = _LangfuseTracer(client)
    trace = client.trace.return_value

    with run_context(run_id="r", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="parser_v1"):
            pass

    trace.span.assert_called_once()
    trace.generation.assert_not_called()


def test_llm_prefixed_span_goes_to_parent_generation():
    client = _make_client()
    tracer = _LangfuseTracer(client)
    trace = client.trace.return_value

    with run_context(run_id="r", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="llm:gpt-5-mini"):
            pass

    trace.generation.assert_called_once()
    trace.span.assert_not_called()


def test_nested_span_parents_on_enclosing_span_not_trace():
    """The structural fix from #111: strategy spans inside a product span
    must become children of the product span, not siblings under the trace."""
    client = _make_client()
    tracer = _LangfuseTracer(client)
    trace = client.trace.return_value
    product_span = MagicMock(name="product_span")
    trace.span.return_value = product_span

    with run_context(run_id="r", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="product:p-1"):
            with tracer.span(name="parser_v1"):
                pass
            with tracer.span(name="llm:gpt-5-mini"):
                pass

    # The product span is created on the trace.
    trace.span.assert_called_once()
    # The two children are created on the product span, not on the trace.
    product_span.span.assert_called_once()
    product_span.generation.assert_called_once()


def test_trace_cache_survives_across_workers_same_run():
    """Same run_id → same trace. Different run_id → different trace."""
    client = _make_client()
    tracer = _LangfuseTracer(client)

    with run_context(run_id="r1", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="parser_v1"):
            pass
    with run_context(run_id="r1", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="specialist_v1"):
            pass
    with run_context(run_id="r2", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="parser_v1"):
            pass

    assert client.trace.call_count == 2
    trace_ids = [c.kwargs["id"] for c in client.trace.call_args_list]
    assert trace_ids == ["r1", "r2"]


def test_no_run_context_falls_back_to_client_level():
    """Without a run_context the tracer should still work — no trace created,
    spans land on the client directly (legacy behavior)."""
    client = _make_client()
    tracer = _LangfuseTracer(client)

    with tracer.span(name="parser_v1"):
        pass
    with tracer.span(name="llm:gpt-5"):
        pass

    client.trace.assert_not_called()
    client.span.assert_called_once()
    client.generation.assert_called_once()


# ---------------------------------------------------------------------------
# #115 rec #8 — run-level scores via score_run()
# ---------------------------------------------------------------------------


def test_score_run_posts_one_score_per_metric():
    client = _make_client()
    tracer = _LangfuseTracer(client)
    ctx = tracing.RunContext(run_id="r-score", merchant_id="m", kg_strategy="k")

    tracer.score_run(
        ctx,
        {
            "raw_coverage_pct": 0.61,
            "new_columns_created": 42,
            "generated_share_pct": 0.51,
        },
    )

    assert client.score.call_count == 3
    posted = {c.kwargs["name"]: c.kwargs for c in client.score.call_args_list}
    assert posted["raw_coverage_pct"]["trace_id"] == "r-score"
    assert posted["raw_coverage_pct"]["value"] == 0.61
    assert posted["new_columns_created"]["value"] == 42


def test_score_run_ensures_trace_exists_even_without_prior_spans():
    """A zero-span run should still materialize a trace so scores have a home."""
    client = _make_client()
    tracer = _LangfuseTracer(client)
    ctx = tracing.RunContext(run_id="r-empty", merchant_id="m", kg_strategy="k")

    tracer.score_run(ctx, {"raw_coverage_pct": 0.0})

    client.trace.assert_called_once()
    client.score.assert_called_once()


def test_score_run_swallows_per_score_failures():
    """One failing score must not block the rest — tracing never breaks runs."""
    client = _make_client()
    client.score.side_effect = [RuntimeError("boom"), None]
    tracer = _LangfuseTracer(client)
    ctx = tracing.RunContext(run_id="r-fail", merchant_id="m", kg_strategy="k")

    # Must not raise.
    tracer.score_run(ctx, {"a": 1, "b": 2})
    assert client.score.call_count == 2


def test_jsonl_tracer_mirrors_run_scores(tmp_path):
    from merchant_agent.enrichment.tracing import _JsonlTracer

    tracer = _JsonlTracer(root=tmp_path)
    ctx = tracing.RunContext(run_id="r-jsonl", merchant_id="m", kg_strategy="k")

    tracer.score_run(ctx, {"raw_coverage_pct": 0.42, "new_columns_created": 7})

    import json as _json
    lines = (tmp_path / "r-jsonl.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = _json.loads(lines[0])
    assert record["record_type"] == "run_scores"
    assert record["run_id"] == "r-jsonl"
    assert record["scores"] == {"raw_coverage_pct": 0.42, "new_columns_created": 7}


def test_noop_tracer_score_run_is_silent():
    t = tracing._NoopTracer()
    ctx = tracing.RunContext(run_id="r", merchant_id="m", kg_strategy="k")
    # Must not raise, must not do anything observable.
    t.score_run(ctx, {"x": 1})


# ---------------------------------------------------------------------------
# #93 — Langfuse required outside of tests
# ---------------------------------------------------------------------------


def test_build_tracer_raises_when_langfuse_unset_outside_tests(monkeypatch):
    """Silent degradation to noop was the issue from #93: in prod we'd
    discover the observability gap only when we needed traces. Now the
    process refuses to start without an explicit tracer config."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("ENRICHMENT_TRACE_JSONL", raising=False)
    monkeypatch.delenv("ENRICHMENT_TRACE_DISABLED", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
        tracing.build_tracer()


def test_build_tracer_allows_noop_when_explicitly_disabled(monkeypatch):
    """Local dev escape hatch — operator explicitly opts out."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("ENRICHMENT_TRACE_JSONL", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ENRICHMENT_TRACE_DISABLED", "1")

    t = tracing.build_tracer()
    assert t.enabled is False


def test_build_tracer_allows_noop_inside_pytest(monkeypatch):
    """Test env is the other escape hatch — PYTEST_CURRENT_TEST is set
    automatically by pytest during test execution."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("ENRICHMENT_TRACE_JSONL", raising=False)
    monkeypatch.delenv("ENRICHMENT_TRACE_DISABLED", raising=False)
    # PYTEST_CURRENT_TEST is already set by the runner; don't clobber.

    t = tracing.build_tracer()
    assert t.enabled is False


def test_build_tracer_allows_jsonl_only_outside_tests(monkeypatch):
    """JSONL alone counts as 'a tracer is configured' — no Langfuse required."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("ENRICHMENT_TRACE_DISABLED", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ENRICHMENT_TRACE_JSONL", "1")

    t = tracing.build_tracer()
    assert t.enabled is True
