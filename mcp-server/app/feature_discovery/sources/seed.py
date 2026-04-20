"""Seed source — loads hand-curated example queries from a JSONL file.

Used for offline runs, unit tests, and as a replay corpus so the
pipeline is reproducible without hitting Reddit / storefronts.

Each line is a JSON object with: source_id, product_type, text. Optional:
source_url, author. fetched_at is stamped at load time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.feature_discovery.types import UserQuery


_DEFAULT_SEED_DIR = Path(__file__).resolve().parents[1] / "seeds"


def harvest_seed(
    *,
    product_type: str,
    max_queries: int,
    seed_dir: Path | None = None,
) -> list[UserQuery]:
    seed_dir = seed_dir or _DEFAULT_SEED_DIR
    path = seed_dir / f"{product_type}.jsonl"
    if not path.exists():
        return []
    now = datetime.now(timezone.utc)
    out: list[UserQuery] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get("product_type") != product_type:
                continue
            out.append(
                UserQuery(
                    source="seed",
                    source_url=data.get("source_url"),
                    source_id=data["source_id"],
                    product_type=product_type,
                    text=data["text"],
                    author=data.get("author"),
                    fetched_at=now,
                )
            )
            if len(out) >= max_queries:
                break
    return out
