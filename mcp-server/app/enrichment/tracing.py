"""Langfuse tracing wrapper. Falls back to a no-op when LANGFUSE_PUBLIC_KEY
is unset or the langfuse package isn't installed, so tests and dev runs
don't need any tracing infra.

Usage from BaseEnrichmentAgent.run():

    with tracer.span(name="parser_v1", input=product.model_dump()) as span:
        ...
        span.update(output=output.model_dump(), cost_usd=cost)
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import Any, Iterator

logger = logging.getLogger(__name__)


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
    def span(self, *, name: str, input: Any | None = None) -> Iterator[_NoopSpan]:
        yield _NoopSpan()

    def flush(self) -> None:
        pass


class _LangfuseTracer:
    """Thin adapter over the langfuse SDK. Lazy-imports so the package is
    optional. Each span maps to a langfuse generation/event."""

    enabled = True

    def __init__(self, client: Any) -> None:
        self._client = client

    @contextlib.contextmanager
    def span(self, *, name: str, input: Any | None = None) -> Iterator[Any]:
        gen = self._client.generation(name=name, input=input)
        try:
            yield gen
        except Exception as exc:
            try:
                gen.update(level="ERROR", status_message=str(exc))
            finally:
                gen.end()
            raise
        else:
            gen.end()

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001 - flush is best-effort
            pass


def build_tracer() -> _NoopTracer | _LangfuseTracer:
    """Construct the tracer for this process. No-op unless LANGFUSE_PUBLIC_KEY
    AND the langfuse SDK are present."""
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return _NoopTracer()
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError:
        logger.info("langfuse not installed — enrichment tracing disabled")
        return _NoopTracer()
    try:
        client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        return _LangfuseTracer(client)
    except Exception as exc:  # noqa: BLE001 - never let tracing break enrichment
        logger.warning("langfuse init failed (%s) — falling back to no-op", exc)
        return _NoopTracer()


# Module-level singleton; cheap to import.
_TRACER: _NoopTracer | _LangfuseTracer | None = None


def get_tracer() -> _NoopTracer | _LangfuseTracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = build_tracer()
    return _TRACER


def _reset_for_tests() -> None:
    global _TRACER
    _TRACER = None
