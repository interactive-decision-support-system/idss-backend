"""OpenAI wrapper used by every enrichment agent.

Centralizes:
  - tiered model selection (see #83):
      * per-product strategies   -> gpt-5-mini   (ENRICHMENT_DEFAULT_MODEL)
      * composer / assessor      -> gpt-5        (ENRICHMENT_LARGE_MODEL,
                                                  ENRICHMENT_COMPOSER_MODEL)
      * utility / pre-pass calls -> gpt-5-nano   (ENRICHMENT_UTILITY_MODEL)
  - retry on transient errors (rate limits, timeouts)
  - cost metering (per-call usage and a process-level running total)
  - JSON-mode parsing convenience

Lazy-imports openai so unit tests can monkey-patch a fake client.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Per-1K-token rates (USD). Only used for the in-process running total —
# Langfuse is the source of truth for server-side cost, so a stale entry
# here skews the CLI summary but not any invoicing. Source:
# https://openai.com/api/pricing/ — entries updated 2026-04-20. Re-check
# when OpenAI announces pricing changes; a stale row silently under- or
# over-reports cost in scripts/summaries.
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_per_1k, output_per_1k)
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o": (0.00250, 0.01000),
    # GPT-5 family — tiered adoption per issue #83.
    "gpt-5-nano": (0.00005, 0.00040),
    "gpt-5-mini": (0.00025, 0.00200),
    "gpt-5": (0.00125, 0.01000),
}


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    parsed_json: Any | None = None


@dataclass
class CostLedger:
    """Process-level running tally. Reset between runs by the orchestrator."""

    total_usd: float = 0.0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)

    def record(self, response: LLMResponse) -> None:
        self.total_usd += response.cost_usd
        self.calls += 1
        self.by_model[response.model] = self.by_model.get(response.model, 0.0) + response.cost_usd

    def reset(self) -> None:
        self.total_usd = 0.0
        self.calls = 0
        self.by_model = {}


_LEDGER = CostLedger()


def get_ledger() -> CostLedger:
    return _LEDGER


def default_model(*, large: bool = False) -> str:
    if large:
        return os.getenv("ENRICHMENT_LARGE_MODEL", "gpt-5")
    return os.getenv("ENRICHMENT_DEFAULT_MODEL", "gpt-5-mini")


def composer_model() -> str:
    """Top-tier model for the composer agent (cross-agent synthesis +
    no-hallucination policy enforcement). Falls back to ENRICHMENT_LARGE_MODEL
    so sites that haven't set the composer knob still land on gpt-5."""
    return os.getenv(
        "ENRICHMENT_COMPOSER_MODEL",
        os.getenv("ENRICHMENT_LARGE_MODEL", "gpt-5"),
    )


def utility_model() -> str:
    """Cheapest tier for utility / pre-pass calls (e.g. the assessor's
    product-type discovery) where high reasoning isn't needed."""
    return os.getenv("ENRICHMENT_UTILITY_MODEL", "gpt-5-nano")


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _PRICING.get(model)
    if not rates:
        return 0.0
    in_rate, out_rate = rates
    return (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate


class LLMClient:
    """Thin facade over the OpenAI chat-completions API."""

    def __init__(self, openai_client: Any | None = None) -> None:
        if openai_client is not None:
            self._client = openai_client
            return
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self._client = None
            return
        try:
            from openai import OpenAI

            # Without an explicit timeout the OpenAI SDK uses httpx.Timeout(None, connect=5.0):
            # a stuck request never raises, so a single hung worker blocks the entire
            # ThreadPoolExecutor drain (#108). Any positive value is enough to convert
            # the hang into an exception that the retry envelope at complete() handles.
            timeout_s = float(os.getenv("ENRICHMENT_OPENAI_TIMEOUT_S", "180"))
            self._client = OpenAI(api_key=api_key, timeout=timeout_s)
        except ImportError:
            logger.warning("openai package not installed — LLMClient is no-op")
            self._client = None
        except Exception as exc:  # noqa: BLE001 - never let init crash module load
            logger.warning("openai client init failed (%s) — LLMClient is no-op", exc)
            self._client = None

    # ------------------------------------------------------------------

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        json_mode: bool = False,
        max_tokens: int = 600,
        temperature: float = 0.2,
        max_retries: int = 3,
    ) -> LLMResponse:
        if self._client is None:
            raise RuntimeError("LLMClient: openai not installed")

        model_name = model or default_model()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        # GPT-5 family only accepts the default temperature (1). Omit the
        # kwarg so OpenAI applies its default; pre-GPT-5 models still honor
        # the caller-supplied value.
        if not model_name.startswith("gpt-5"):
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        # Child span per LLM call — parent is whichever agent span opened
        # in BaseEnrichmentAgent.run(). When tracing is off, this is a
        # zero-cost _NoopSpan; when on, Langfuse shows the full prompt,
        # response, and per-call cost/latency/tokens.
        from app.enrichment.tracing import get_tracer

        tracer = get_tracer()
        span_input = (
            {"system": system, "user": user, "json_mode": json_mode}
            if tracer.enabled
            else {"model": model_name}
        )
        last_err: Exception | None = None
        with tracer.span(name=f"llm:{model_name}", input=span_input) as span:
            for attempt in range(max_retries):
                start = time.perf_counter()
                try:
                    resp = self._client.chat.completions.create(**kwargs)
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    text = (resp.choices[0].message.content or "").strip()
                    usage = getattr(resp, "usage", None)
                    in_tok = getattr(usage, "prompt_tokens", 0) or 0
                    out_tok = getattr(usage, "completion_tokens", 0) or 0
                    cost = _estimate_cost(model_name, in_tok, out_tok)
                    parsed = None
                    if json_mode:
                        try:
                            parsed = json.loads(text) if text else None
                        except json.JSONDecodeError as exc:
                            raise ValueError(
                                f"json_mode response was not valid JSON: {exc}"
                            ) from exc
                    response = LLMResponse(
                        text=text,
                        model=model_name,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cost_usd=cost,
                        latency_ms=latency_ms,
                        parsed_json=parsed,
                    )
                    _LEDGER.record(response)
                    span.update(
                        output=text if tracer.enabled else {"len": len(text)},
                        metadata={
                            "model": model_name,
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "cost_usd": cost,
                            "latency_ms": latency_ms,
                            "attempt": attempt + 1,
                            "json_mode": json_mode,
                        },
                    )
                    return response
                except Exception as exc:  # noqa: BLE001 - retry envelope
                    last_err = exc
                    # Cheap backoff: 0.5s, 1s, 2s.
                    if attempt < max_retries - 1:
                        time.sleep(0.5 * (2**attempt))
                    continue
            # Exhausted retries — record failure on the span before raising.
            span.update(
                level="ERROR",
                status_message=str(last_err),
                metadata={"model": model_name, "attempts": max_retries},
            )
        raise RuntimeError(
            f"LLMClient.complete failed after {max_retries} retries: {last_err}"
        )
