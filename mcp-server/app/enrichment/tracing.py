"""Enrichment tracing — Langfuse adapter + no-op fallback + optional JSONL mirror.

Usage from BaseEnrichmentAgent.run():

    with tracer.span(name="parser_v1", input=product.model_dump()) as span:
        ...
        span.update(output=output.model_dump(), metadata={"latency_ms": 42})

Run-level grouping (read by the tracer when opening spans) comes from a
contextvar populated by the orchestrator:

    with run_context(run_id=..., merchant_id=..., kg_strategy=...):
        run_enrichment(...)

Every span opened inside that block is tagged with ``run:<id>``,
``merchant:<id>``, ``kg_strategy:<s>``. This is what the Streamlit
inspector and the KG-coverage metric scope on.

When LANGFUSE_PUBLIC_KEY is unset and ENRICHMENT_TRACE_JSONL != "1",
``get_tracer()`` returns ``_NoopTracer`` — the contract asserted by
tests/enrichment/test_base.py stays unchanged (``enabled is False``,
``span.id`` truthy, ``span.update/end`` no-op).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run context (run_id / merchant_id / kg_strategy) used for tagging
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunContext:
    run_id: str
    merchant_id: str
    kg_strategy: str


_RUN_CONTEXT: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "enrichment_run_context", default=None
)


@contextlib.contextmanager
def run_context(
    *, run_id: str, merchant_id: str, kg_strategy: str
) -> Iterator[RunContext]:
    """Populate the contextvar read by the active tracer when opening spans."""
    ctx = RunContext(run_id=run_id, merchant_id=merchant_id, kg_strategy=kg_strategy)
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)


def get_run_context() -> RunContext | None:
    return _RUN_CONTEXT.get()


def _current_tags() -> list[str]:
    ctx = _RUN_CONTEXT.get()
    if ctx is None:
        return []
    return [
        f"run:{ctx.run_id}",
        f"merchant:{ctx.merchant_id}",
        f"kg_strategy:{ctx.kg_strategy}",
    ]


# ---------------------------------------------------------------------------
# No-op tracer — default when nothing is configured
# ---------------------------------------------------------------------------


class _NoopSpan:
    """Drop-in span when tracing is disabled."""

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex

    def update(self, **_: Any) -> None:
        pass

    def end(self) -> None:
        pass


class _NoopTracer:
    enabled = False

    @contextlib.contextmanager
    def span(
        self, *, name: str, input: Any | None = None, metadata: Any | None = None
    ) -> Iterator[_NoopSpan]:
        yield _NoopSpan()

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Langfuse adapter — opens a generation per span, applies run-context tags
# ---------------------------------------------------------------------------


class _LangfuseSpan:
    """Wraps a langfuse span/generation so its ``update`` signature matches
    what callers pass (``output``, ``metadata``, ``cost_usd``, ``level``,
    ``status_message``) regardless of the underlying SDK kwargs."""

    def __init__(self, node: Any) -> None:
        self._node = node
        self.id = getattr(node, "id", None) or uuid.uuid4().hex

    def update(self, **kwargs: Any) -> None:
        try:
            self._node.update(**kwargs)
        except Exception as exc:  # noqa: BLE001 - tracing must never raise
            logger.debug("langfuse span.update failed: %s", exc)

    def end(self) -> None:
        try:
            self._node.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse span.end failed: %s", exc)


# Per-run trace cache + parent-span stack.
#
# Issue #111: Langfuse v2 treats ``tags=`` as a trace-level field; passing it
# to ``generation()`` is silently dropped. We therefore maintain one trace per
# run (keyed by run_id) with trace-level tags, and nest every span/generation
# under it. The ContextVar stack makes the enclosing span the implicit parent,
# so strategy spans inside a ``product:<id>`` span become its children.
_LANGFUSE_TRACES: dict[str, Any] = {}
_LANGFUSE_TRACES_LOCK = threading.Lock()
_LANGFUSE_PARENT_STACK: contextvars.ContextVar[tuple[Any, ...]] = contextvars.ContextVar(
    "enrichment_lf_parent_stack", default=()
)


def _current_parent() -> Any | None:
    stack = _LANGFUSE_PARENT_STACK.get()
    return stack[-1] if stack else None


class _LangfuseTracer:
    """Adapter over the langfuse SDK. Lazy-imports so the package is optional.

    Tree shape:
        trace (per run_id, trace-level tags)
          ├── span product:<id>
          │     ├── span parser_v1
          │     │     └── generation llm:gpt-5-mini
          │     └── ...
          └── ...

    ``name.startswith("llm:")`` dispatches to ``generation()``; everything
    else is a generic ``span()``.
    """

    enabled = True

    def __init__(self, client: Any) -> None:
        self._client = client

    def _get_or_create_trace(self, run_ctx: RunContext) -> Any | None:
        with _LANGFUSE_TRACES_LOCK:
            if run_ctx.run_id in _LANGFUSE_TRACES:
                return _LANGFUSE_TRACES[run_ctx.run_id]
            try:
                trace = self._client.trace(
                    id=run_ctx.run_id,
                    name=f"enrichment_run:{run_ctx.merchant_id}:{run_ctx.run_id[:8]}",
                    tags=[
                        f"run:{run_ctx.run_id}",
                        f"merchant:{run_ctx.merchant_id}",
                        f"kg_strategy:{run_ctx.kg_strategy}",
                    ],
                    metadata={
                        "merchant_id": run_ctx.merchant_id,
                        "kg_strategy": run_ctx.kg_strategy,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - never break enrichment
                logger.debug("langfuse trace() failed: %s", exc)
                trace = None
            _LANGFUSE_TRACES[run_ctx.run_id] = trace
            return trace

    @contextlib.contextmanager
    def span(
        self, *, name: str, input: Any | None = None, metadata: Any | None = None
    ) -> Iterator[_LangfuseSpan]:
        run_ctx = _RUN_CONTEXT.get()
        is_llm = name.startswith("llm:")

        if run_ctx is not None:
            trace = self._get_or_create_trace(run_ctx)
            parent = _current_parent() or trace or self._client
        else:
            # No run context — fall back to client-level node (legacy behavior).
            parent = self._client

        kwargs: dict[str, Any] = {"name": name, "input": input}
        if metadata is not None:
            kwargs["metadata"] = metadata
        try:
            if is_llm:
                node = parent.generation(**kwargs)
            else:
                node = parent.span(**kwargs)
        except Exception as exc:  # noqa: BLE001 - never break enrichment
            logger.debug("langfuse span/generation create failed: %s — noop", exc)
            yield _LangfuseSpan(_NoopSpan())
            return

        span = _LangfuseSpan(node)
        token = _LANGFUSE_PARENT_STACK.set(_LANGFUSE_PARENT_STACK.get() + (node,))
        try:
            yield span
        except Exception as exc:
            span.update(level="ERROR", status_message=str(exc))
            span.end()
            _LANGFUSE_PARENT_STACK.reset(token)
            raise
        else:
            span.end()
            _LANGFUSE_PARENT_STACK.reset(token)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001 - flush is best-effort
            pass


# ---------------------------------------------------------------------------
# Optional JSONL mirror — zero-dep fallback for PR screenshots / CI replay
# ---------------------------------------------------------------------------


class _JsonlSpan:
    """Accumulates update() kwargs and flushes one JSON line on end()."""

    def __init__(self, tracer: "_JsonlTracer", name: str, input: Any) -> None:
        self._tracer = tracer
        self.id = uuid.uuid4().hex
        self._record: dict[str, Any] = {
            "span_id": self.id,
            "name": name,
            "input": _safe_jsonable(input),
            "tags": _current_tags(),
            "started_at": time.time(),
            "updates": [],
        }

    def update(self, **kwargs: Any) -> None:
        self._record["updates"].append(_safe_jsonable(kwargs))

    def end(self) -> None:
        self._record["ended_at"] = time.time()
        self._tracer._write(self._record)


class _JsonlTracer:
    """Appends every span as a JSON line to
    ``logs/enrichment_traces/<run_id>.jsonl`` (or ``no_run.jsonl`` outside
    a run_context). Enabled by ENRICHMENT_TRACE_JSONL=1. No external deps."""

    enabled = True

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self) -> Path:
        ctx = _RUN_CONTEXT.get()
        run_id = ctx.run_id if ctx is not None else "no_run"
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root / f"{run_id}.jsonl"

    def _write(self, record: dict[str, Any]) -> None:
        path = self._path()
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 - never break enrichment
            logger.debug("jsonl tracer write failed: %s", exc)

    @contextlib.contextmanager
    def span(
        self, *, name: str, input: Any | None = None, metadata: Any | None = None
    ) -> Iterator[_JsonlSpan]:
        span = _JsonlSpan(self, name, input)
        if metadata is not None:
            span.update(metadata=metadata)
        try:
            yield span
        except Exception as exc:
            span.update(level="ERROR", status_message=str(exc))
            span.end()
            raise
        else:
            span.end()

    def flush(self) -> None:
        pass


def _safe_jsonable(value: Any) -> Any:
    """Best-effort convert to a JSON-serializable structure."""
    try:
        json.dumps(value, default=str)
        return value
    except TypeError:
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:  # noqa: BLE001
            return repr(value)


# ---------------------------------------------------------------------------
# Composite tracer — fans out to both Langfuse and JSONL when both are on
# ---------------------------------------------------------------------------


class _CompositeSpan:
    def __init__(self, spans: list[Any]) -> None:
        self._spans = spans
        self.id = next((getattr(s, "id", None) for s in spans if getattr(s, "id", None)), uuid.uuid4().hex)

    def update(self, **kwargs: Any) -> None:
        for s in self._spans:
            try:
                s.update(**kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.debug("composite span.update failed: %s", exc)

    def end(self) -> None:
        for s in self._spans:
            try:
                s.end()
            except Exception as exc:  # noqa: BLE001
                logger.debug("composite span.end failed: %s", exc)


class _CompositeTracer:
    enabled = True

    def __init__(self, tracers: list[Any]) -> None:
        self._tracers = tracers

    @contextlib.contextmanager
    def span(
        self, *, name: str, input: Any | None = None, metadata: Any | None = None
    ) -> Iterator[_CompositeSpan]:
        stack = contextlib.ExitStack()
        try:
            spans = [
                stack.enter_context(t.span(name=name, input=input, metadata=metadata))
                for t in self._tracers
            ]
            yield _CompositeSpan(spans)
        finally:
            stack.close()

    def flush(self) -> None:
        for t in self._tracers:
            try:
                t.flush()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_tracer() -> Any:
    """Construct the tracer for this process.

    - Langfuse tracer when LANGFUSE_PUBLIC_KEY is set AND the SDK is installed.
    - JSONL tracer when ENRICHMENT_TRACE_JSONL=1 (can run alongside Langfuse).
    - NoopTracer otherwise.
    """
    tracers: list[Any] = []

    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]

            client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
            tracers.append(_LangfuseTracer(client))
        except ImportError:
            logger.info("langfuse not installed — enrichment tracing disabled")
        except Exception as exc:  # noqa: BLE001 - never let tracing break enrichment
            logger.warning("langfuse init failed (%s) — falling back to no-op", exc)

    if os.getenv("ENRICHMENT_TRACE_JSONL") == "1":
        root = Path(os.getenv("ENRICHMENT_TRACE_JSONL_DIR", "logs/enrichment_traces"))
        tracers.append(_JsonlTracer(root))

    if not tracers:
        return _NoopTracer()
    if len(tracers) == 1:
        return tracers[0]
    return _CompositeTracer(tracers)


# Module-level singleton; cheap to import.
_TRACER: Any | None = None


def get_tracer() -> Any:
    global _TRACER
    if _TRACER is None:
        _TRACER = build_tracer()
    return _TRACER


def _reset_for_tests() -> None:
    global _TRACER
    _TRACER = None
    with _LANGFUSE_TRACES_LOCK:
        _LANGFUSE_TRACES.clear()
