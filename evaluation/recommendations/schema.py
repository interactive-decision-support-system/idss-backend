"""
Golden truth schema for recommendation evaluation.

Each item: user_query, expected_ucp (filters matching get_search_filters / ProductFilters),
ground_truth_product_ids (list of real product IDs that are correct/best matches).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GoldenItem:
    """Single golden example for evaluation."""
    query_id: str
    user_query: str
    expected_ucp: Dict[str, Any]  # Hard/soft filters: category, product_type, brand, price_max_cents, etc.
    ground_truth_product_ids: List[str]  # ~10 real product UUIDs from DB considered correct matches

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "user_query": self.user_query,
            "expected_ucp": self.expected_ucp,
            "ground_truth_product_ids": self.ground_truth_product_ids,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GoldenItem":
        return cls(
            query_id=str(d["query_id"]),
            user_query=str(d["user_query"]),
            expected_ucp=dict(d.get("expected_ucp") or {}),
            ground_truth_product_ids=list(d.get("ground_truth_product_ids") or []),
        )


def load_golden_dataset(path: str) -> List[GoldenItem]:
    """Load golden dataset from JSON file."""
    import json
    with open(path, "r") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("items", data.get("queries", []))
    return [GoldenItem.from_dict(it) for it in items]
