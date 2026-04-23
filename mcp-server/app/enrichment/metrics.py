"""Per-run enrichment metrics — deterministic aggregates for Langfuse scoring.

Computes 6 scores from raw input + composer outputs after a run completes:

    raw_coverage_pct        — filled raw cells / (products × raw columns)
    new_columns_created     — count of composed keys not in raw input
    new_column_coverage_pct — filled new-col cells / (products × new columns)
    parsed_share_pct        — raw_parse / (parsed + generated) over new cols
    generated_share_pct     — parametric / (parsed + generated) over new cols
    singleton_column_count  — new columns filled on exactly one product

Raw columns per user decision: only fields present in the uploaded catalog —
the 7 fixed ``ProductInput`` scalars plus any key observed in
``raw_attributes`` across the loaded products. No schema union.

Issue #115 rec #8.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, TypedDict

from app.enrichment.types import ProductInput, StrategyOutput

logger = logging.getLogger(__name__)


# Columns that always exist in ``merchants.products_<merchant>`` — match the
# scalar fields on ``ProductInput``. Extra keys live in ``raw_attributes``.
_SCALAR_COLUMNS: tuple[str, ...] = (
    "product_id",
    "title",
    "category",
    "brand",
    "description",
    "price",
    "link",
)


class RunMetrics(TypedDict, total=False):
    raw_coverage_pct: float
    new_columns_created: int
    new_column_coverage_pct: float
    parsed_share_pct: float
    generated_share_pct: float
    singleton_column_count: int
    # Scraper (issue #118) run-level aggregates
    scraper_products_gated_in: int
    scraper_products_gated_out: int
    scraper_fields_attempted: int
    scraper_fields_filled: int
    scraper_fields_conflicted_with_parser: int
    scraper_search_calls: int
    scraper_crawl_calls: int
    scraper_cost_usd: float
    scraper_allowlist_blocks: int
    scraper_robots_blocks: int


def _is_substantive(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) > 0
    return True


def _raw_input_columns(products: Iterable[ProductInput]) -> set[str]:
    cols: set[str] = set(_SCALAR_COLUMNS)
    for p in products:
        cols.update(p.raw_attributes.keys())
    return cols


def _raw_filled_cells(products: Iterable[ProductInput], raw_cols: set[str]) -> int:
    filled = 0
    for p in products:
        for col in raw_cols:
            if col in _SCALAR_COLUMNS:
                if _is_substantive(getattr(p, col, None)):
                    filled += 1
            elif _is_substantive(p.raw_attributes.get(col)):
                filled += 1
    return filled


def _composer_output(outputs: Iterable[StrategyOutput]) -> StrategyOutput | None:
    for out in outputs:
        if out.strategy == "composer_v1":
            return out
    return None


def compute_run_metrics(
    products: list[ProductInput],
    outputs_by_pid: dict[Any, list[StrategyOutput]],
) -> RunMetrics:
    """Compute the 6 run-level scores. Returns ``{}`` for an empty run."""
    if not products:
        return {}

    raw_cols = _raw_input_columns(products)
    n = len(products)
    raw_denominator = n * len(raw_cols)
    raw_filled = _raw_filled_cells(products, raw_cols)

    composed_by_pid: dict[Any, dict[str, Any]] = {}
    decisions_by_pid: dict[Any, list[dict[str, Any]]] = {}
    for pid, outs in outputs_by_pid.items():
        comp = _composer_output(outs)
        if comp is None:
            continue
        composed_by_pid[pid] = comp.attributes.get("composed_fields") or {}
        decisions_by_pid[pid] = comp.attributes.get("composer_decisions") or []

    all_composed_keys: set[str] = set()
    for fields in composed_by_pid.values():
        all_composed_keys.update(fields.keys())
    new_cols = all_composed_keys - raw_cols
    new_columns_created = len(new_cols)

    metrics: RunMetrics = {
        "raw_coverage_pct": (raw_filled / raw_denominator) if raw_denominator else 0.0,
        "new_columns_created": new_columns_created,
    }

    if new_columns_created == 0:
        metrics.update(compute_scraper_metrics(products, outputs_by_pid))
        return metrics

    filled_new_cells = 0
    per_col_fill: dict[str, int] = {k: 0 for k in new_cols}
    for fields in composed_by_pid.values():
        for k in new_cols:
            if _is_substantive(fields.get(k)):
                filled_new_cells += 1
                per_col_fill[k] += 1

    denom_new = n * new_columns_created
    metrics["new_column_coverage_pct"] = filled_new_cells / denom_new

    parsed = 0
    generated = 0
    for decs in decisions_by_pid.values():
        for d in decs:
            if d.get("key") not in new_cols:
                continue
            kind = d.get("source_kind")
            if kind == "raw_parse":
                parsed += 1
            elif kind == "parametric":
                generated += 1
    total_classified = parsed + generated
    if total_classified:
        metrics["parsed_share_pct"] = parsed / total_classified
        metrics["generated_share_pct"] = generated / total_classified
    else:
        metrics["parsed_share_pct"] = 0.0
        metrics["generated_share_pct"] = 0.0

    metrics["singleton_column_count"] = sum(1 for v in per_col_fill.values() if v == 1)

    # Scraper (#118) run-level aggregates — computed from the same
    # outputs_by_pid dict so no extra run-time bookkeeping is needed.
    metrics.update(compute_scraper_metrics(products, outputs_by_pid))
    return metrics


# ---------------------------------------------------------------------------
# Scraper (issue #118) aggregates
# ---------------------------------------------------------------------------


def _scraper_output(outputs: Iterable[StrategyOutput]) -> StrategyOutput | None:
    for out in outputs:
        if out.strategy == "scraper_v1":
            return out
    return None


def _parser_output(outputs: Iterable[StrategyOutput]) -> StrategyOutput | None:
    for out in outputs:
        if out.strategy == "parser_v1":
            return out
    return None


def compute_scraper_metrics(
    products: list[ProductInput],
    outputs_by_pid: dict[Any, list[StrategyOutput]],
) -> dict[str, Any]:
    """Aggregate the scraper-v2 telemetry over one run.

    Inputs read:
      - scraper_v1 StrategyOutput per product (scraped_specs, notes)
      - parser_v1 StrategyOutput per product (conflict detection)
      - scraper log JSONL (for allowlist / robots block counts)

    ``scraper_fields_attempted`` is the total number of missing-fields
    the scraper saw across all products it was gated *in* on. Derived
    from ``scraped_specs + scraped_missing_on_exit`` isn't available —
    we approximate with ``scraped_specs`` + the per-product drop in
    scraped_sources queries (one query per missing field).
    """
    gated_in = 0
    gated_out = 0
    fields_attempted = 0
    fields_filled = 0
    fields_conflicted = 0
    search_calls = 0
    crawl_calls = 0

    for pid, outs in outputs_by_pid.items():
        scr = _scraper_output(outs)
        if scr is None:
            continue
        notes = (scr.notes or "")
        attrs = scr.attributes or {}
        sources = attrs.get("scraped_sources") or []
        specs = attrs.get("scraped_specs") or {}
        # Gating: any non-empty "notes" starting with no_ / websearch_ means
        # the scraper decided not to do work on this product.
        if notes in ("no_missing_fields", "websearch_disabled", "websearch_no_api_key"):
            gated_out += 1
            continue
        gated_in += 1
        # Distinct queries in sources ≈ search_calls; pages fetched ==
        # sources length; attempted fields == distinct queries (one per
        # missing field up to budget).
        queries = {s.get("query") for s in sources if isinstance(s, dict)}
        search_calls += len(queries)
        crawl_calls += len(sources)
        fields_attempted += len(queries)
        fields_filled += len(specs)

        # Conflict with parser: parser_specs has the key and its value
        # differs from the scraped value.
        par = _parser_output(outs)
        parsed_specs: dict[str, Any] = {}
        if par is not None:
            ps = (par.attributes or {}).get("parsed_specs")
            if isinstance(ps, dict):
                parsed_specs = ps
        for key, entry in specs.items():
            if key not in parsed_specs:
                continue
            scraped_val = entry.get("value") if isinstance(entry, dict) else entry
            if parsed_specs[key] != scraped_val:
                fields_conflicted += 1

    # Allowlist / robots blocks come from the scraper_client log — best-
    # effort, absent in tests that don't write the log.
    allowlist_blocks, robots_blocks = _count_blocks_from_log()

    out: dict[str, Any] = {
        "scraper_products_gated_in": gated_in,
        "scraper_products_gated_out": gated_out,
        "scraper_fields_attempted": fields_attempted,
        "scraper_fields_filled": fields_filled,
        "scraper_fields_conflicted_with_parser": fields_conflicted,
        "scraper_search_calls": search_calls,
        "scraper_crawl_calls": crawl_calls,
        "scraper_allowlist_blocks": allowlist_blocks,
        "scraper_robots_blocks": robots_blocks,
        # Per-run scraper_cost_usd is carried on the tracer via the
        # per-LLM-call cost accounting; we leave it 0.0 here so the
        # metric schema is always populated but don't double-count it.
        "scraper_cost_usd": 0.0,
    }
    return out


def _count_blocks_from_log() -> tuple[int, int]:
    """Tail the scraper log file and count blocked_* statuses.

    Returns ``(allowlist_blocks, robots_blocks)``. Missing file → (0, 0).
    Bounded to the last ~10000 lines to keep the cost constant across runs.
    """
    try:
        from app.enrichment.tools import scraper_client

        log_path: Path = scraper_client._LOG_PATH  # noqa: SLF001 - intentional
    except Exception:  # noqa: BLE001
        return 0, 0
    if not log_path.exists():
        return 0, 0
    allow = 0
    robots = 0
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()[-10000:]
    except Exception as exc:  # noqa: BLE001
        logger.debug("scraper_log_read_failed: %s", exc)
        return 0, 0
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        status = rec.get("status")
        if status == "blocked_allowlist":
            allow += 1
        elif status == "blocked_robots":
            robots += 1
    return allow, robots
