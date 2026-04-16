"""Base class every enrichment agent inherits from.

Subclasses implement `_invoke(product, context) -> StrategyOutput`. The base
handles tracing, latency/cost capture, output-key validation, and turning
exceptions into AgentResult(success=False, error=...).
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from app.enrichment import registry
from app.enrichment.tracing import get_tracer
from app.enrichment.types import AgentResult, ProductInput, StrategyOutput

logger = logging.getLogger(__name__)


class BaseEnrichmentAgent:
    """All agents declare STRATEGY, OUTPUT_KEYS, DEFAULT_MODEL as class attributes
    and implement _invoke().

    Subclass example:

        @registry.register
        class ParserAgent(BaseEnrichmentAgent):
            STRATEGY = "parser_v1"
            OUTPUT_KEYS = frozenset({"parsed_specs", "parsed_at", "parsed_source_fields"})
            DEFAULT_MODEL = "gpt-4o-mini"

            def _invoke(self, product, context):
                ...
                return StrategyOutput(...)
    """

    STRATEGY: str = ""
    OUTPUT_KEYS: frozenset[str] = frozenset()
    DEFAULT_MODEL: str | None = None

    def __init__(self) -> None:
        if not self.STRATEGY:
            raise TypeError(f"{type(self).__name__} must set STRATEGY")
        if not self.OUTPUT_KEYS:
            raise TypeError(f"{type(self).__name__} must set OUTPUT_KEYS")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, product: ProductInput, context: dict[str, Any] | None = None) -> AgentResult:
        ctx = context or {}
        tracer = get_tracer()
        start = time.perf_counter()
        with tracer.span(name=self.STRATEGY, input={"product_id": str(product.product_id)}) as span:
            try:
                output = self._invoke(product, ctx)
                self._validate_output(output, product.product_id)
                latency_ms = int((time.perf_counter() - start) * 1000)
                cost_usd = ctx.get("_last_cost_usd")
                span.update(
                    output={"keys": sorted(output.attributes.keys())},
                    metadata={"strategy": self.STRATEGY, "latency_ms": latency_ms},
                )
                return AgentResult(
                    success=True,
                    output=output,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    trace_id=getattr(span, "id", None),
                    strategy=self.STRATEGY,
                    product_id=product.product_id,
                )
            except Exception as exc:  # noqa: BLE001 - one agent's failure must not kill the run
                latency_ms = int((time.perf_counter() - start) * 1000)
                logger.warning(
                    "enrichment_agent_failed",
                    extra={
                        "strategy": self.STRATEGY,
                        "product_id": str(product.product_id),
                        "error": str(exc),
                    },
                )
                return AgentResult(
                    success=False,
                    output=None,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=latency_ms,
                    trace_id=getattr(span, "id", None),
                    strategy=self.STRATEGY,
                    product_id=product.product_id,
                )

    # ------------------------------------------------------------------
    # To be implemented by subclasses
    # ------------------------------------------------------------------

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    def _validate_output(self, output: StrategyOutput, product_id: UUID) -> None:
        if output.product_id != product_id:
            raise ValueError(
                f"strategy {self.STRATEGY}: output.product_id ({output.product_id}) "
                f"does not match input ({product_id})"
            )
        if output.strategy != self.STRATEGY:
            raise ValueError(
                f"strategy {self.STRATEGY}: output.strategy is {output.strategy!r}"
            )
        emitted = set(output.attributes.keys())
        # Allow subset (agent may not have populated every key) but reject any
        # key not in OUTPUT_KEYS — that would break the disjoint-keys invariant.
        unknown = emitted - self.OUTPUT_KEYS
        if unknown:
            raise ValueError(
                f"strategy {self.STRATEGY}: emitted keys not declared in OUTPUT_KEYS: "
                f"{sorted(unknown)}"
            )
        # Belt-and-braces: also re-check against the registry's known foreign keys.
        for other_strategy, other_keys in registry.all_known_keys().items():
            if other_strategy == self.STRATEGY:
                continue
            cross = emitted & other_keys
            if cross:
                raise ValueError(
                    f"strategy {self.STRATEGY}: emitted keys collide with {other_strategy!r}: "
                    f"{sorted(cross)}"
                )
