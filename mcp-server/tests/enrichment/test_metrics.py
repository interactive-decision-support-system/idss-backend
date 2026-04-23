"""Tests for the per-run enrichment metrics (issue #115 rec #8).

Unit coverage for ``compute_run_metrics``: the six scores, edge cases
(empty run, no new columns, products missing composer output, decisions
whose keys fall outside the new-column set), and the denominator rule —
raw columns come only from the uploaded catalog, not a schema union.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from app.enrichment.metrics import compute_run_metrics
from app.enrichment.types import ProductInput, StrategyOutput


def _product(
    *,
    pid: UUID | None = None,
    title: str | None = "Laptop",
    brand: str | None = "Acme",
    price: Decimal | None = Decimal("999.00"),
    raw_attributes: dict | None = None,
) -> ProductInput:
    return ProductInput(
        product_id=pid or uuid4(),
        title=title,
        brand=brand,
        price=price,
        raw_attributes=raw_attributes or {},
    )


def _composer(
    pid: UUID,
    *,
    composed_fields: dict,
    decisions: list[dict],
) -> StrategyOutput:
    return StrategyOutput(
        product_id=pid,
        strategy="composer_v1",
        attributes={
            "composed_fields": composed_fields,
            "composer_decisions": decisions,
        },
    )


def test_empty_run_returns_empty_metrics():
    assert compute_run_metrics([], {}) == {}


def test_no_composer_output_still_reports_raw_coverage():
    p = _product()
    metrics = compute_run_metrics([p], {p.product_id: []})
    # Scalars: product_id + title + brand + price are filled (4). Denominator
    # is the 7 fixed scalars; no raw_attributes added.
    assert metrics["new_columns_created"] == 0
    assert metrics["raw_coverage_pct"] == 4 / 7
    assert "new_column_coverage_pct" not in metrics


def test_raw_coverage_denominator_uses_only_uploaded_columns():
    # Two products; one has an extra raw_attributes key, the other doesn't.
    # raw_input_cols = 7 scalars + {"color"} = 8.
    p1 = _product(raw_attributes={"color": "silver"})
    p2 = _product(raw_attributes={})
    metrics = compute_run_metrics([p1, p2], {})
    # Each product fills: product_id, title, brand, price → 4 scalars.
    # p1 also fills color → +1. Total filled = 9. Denominator = 2 × 8 = 16.
    assert metrics["raw_coverage_pct"] == 9 / 16


def test_new_columns_and_coverage():
    p1 = _product()
    p2 = _product()
    composed = {
        "product_id": str(p1.product_id),
        "title": "X",
        "ram_gb": 16,
        "battery_life_hours": 10,
    }
    decisions = [
        {"key": "ram_gb", "source_kind": "raw_parse", "source_strategy": "parser_v1"},
        {"key": "battery_life_hours", "source_kind": "parametric", "source_strategy": "specialist_v1"},
    ]
    outputs = {
        p1.product_id: [_composer(p1.product_id, composed_fields=composed, decisions=decisions)],
        # p2 has no composer output — omitted from composed keys + decisions.
        p2.product_id: [],
    }
    metrics = compute_run_metrics([p1, p2], outputs)
    # New columns: ram_gb, battery_life_hours → 2.
    assert metrics["new_columns_created"] == 2
    # Filled new-col cells: only p1 has them → 2 of (2 products × 2 cols) = 2/4.
    assert metrics["new_column_coverage_pct"] == 2 / 4
    # One parsed, one generated.
    assert metrics["parsed_share_pct"] == 0.5
    assert metrics["generated_share_pct"] == 0.5
    # Both new cols are filled on exactly 1 of 2 products → both singletons.
    assert metrics["singleton_column_count"] == 2


def test_decisions_outside_new_cols_are_excluded_from_shares():
    # A decision keyed on a raw scalar (e.g. "title") should NOT contribute
    # to parsed/generated shares — those are over new columns only.
    p = _product()
    composed = {"product_id": str(p.product_id), "title": "X", "ram_gb": 16}
    decisions = [
        {"key": "title", "source_kind": "parametric", "source_strategy": None},
        {"key": "ram_gb", "source_kind": "raw_parse", "source_strategy": "parser_v1"},
    ]
    outputs = {p.product_id: [_composer(p.product_id, composed_fields=composed, decisions=decisions)]}
    metrics = compute_run_metrics([p], outputs)
    # Only "ram_gb" is a new column. Only its decision counts.
    assert metrics["parsed_share_pct"] == 1.0
    assert metrics["generated_share_pct"] == 0.0


def test_singleton_count_catches_label_explosion():
    # Three products with three distinct "good_for_*" keys — each filled
    # on exactly one product. Classic label-explosion signature.
    p1, p2, p3 = _product(), _product(), _product()
    comp = lambda pid, key: _composer(
        pid,
        composed_fields={"product_id": str(pid), key: 0.9},
        decisions=[{"key": key, "source_kind": "parametric", "source_strategy": "soft_tagger_v1"}],
    )
    outputs = {
        p1.product_id: [comp(p1.product_id, "good_for_gaming")],
        p2.product_id: [comp(p2.product_id, "good_for_aaa_gaming")],
        p3.product_id: [comp(p3.product_id, "good_for_cloud_gaming")],
    }
    metrics = compute_run_metrics([p1, p2, p3], outputs)
    assert metrics["new_columns_created"] == 3
    assert metrics["singleton_column_count"] == 3


def test_shares_are_zero_when_no_decisions_classified():
    p = _product()
    composed = {"ram_gb": 16}
    # Decision with unknown source_kind — neither raw_parse nor parametric.
    decisions = [{"key": "ram_gb", "source_kind": "unknown", "source_strategy": None}]
    outputs = {p.product_id: [_composer(p.product_id, composed_fields=composed, decisions=decisions)]}
    metrics = compute_run_metrics([p], outputs)
    assert metrics["parsed_share_pct"] == 0.0
    assert metrics["generated_share_pct"] == 0.0
