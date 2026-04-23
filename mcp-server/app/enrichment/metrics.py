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

from typing import Any, Iterable, TypedDict

from app.enrichment.types import ProductInput, StrategyOutput


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
    return metrics
