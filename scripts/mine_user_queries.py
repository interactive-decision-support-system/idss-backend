#!/usr/bin/env python3
"""
Prototype: mine third-party shopper demand signal for feature-discovery.

Harvests shopper-authored text from sources that sit outside any
individual storefront (Reddit threads, seed corpus; future: YouTube
comments, external forums, Trends), runs an LLM extractor to pull
out the attributes / use-cases / constraints / preferences each
shopper is reasoning about, aggregates the result across the corpus,
and diffs the discovered feature set against what the existing
``products_enriched`` pipeline currently emits.

Storefront-hosted Q&A / on-site reviews are intentionally excluded —
they are filtered by what a retailer chose to host and leak
merchant-side selection bias into the demand estimate.

Outputs (under runs/feature_discovery/<product_type>/<ts>/):
  raw_queries.jsonl       one UserQuery per line
  extracted.jsonl         one ExtractedFeatures per line
  frequencies.json        aggregated FeatureFrequency list
  coverage.json           CoverageReport (covered / missing / underused)

Examples:
  # Offline dry run using seed corpus + fake LLM (no API key needed):
  python scripts/mine_user_queries.py --product-type laptop --sources seed \
      --fake-llm --catalog-keys-file /tmp/current_keys.json

  # Live: Reddit + seed, real LLM, pull catalog keys from Supabase:
  python scripts/mine_user_queries.py --product-type laptop \
      --sources reddit,seed --max-queries 40 --merchant default
"""


from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_MERCHANT_ID_RE = re.compile(r"^[a-z0-9_]+$")

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp-server"))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from app.feature_discovery.aggregator import (  # noqa: E402
    aggregate,
    collect_catalog_keys,
    coverage,
)
from app.feature_discovery.extractor import FeatureExtractor  # noqa: E402
from app.feature_discovery.sources import harvest  # noqa: E402
from app.feature_discovery.types import ExtractedFeatures, UserQuery  # noqa: E402


logger = logging.getLogger("mine_user_queries")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--product-type", required=True, help="e.g. laptop, headphones")
    p.add_argument(
        "--sources",
        default="seed",
        help="Comma list from: reddit, seed",
    )
    p.add_argument("--max-queries", type=int, default=20)
    p.add_argument(
        "--catalog-keys-file",
        default=None,
        help="JSON file: list[str] of attribute keys currently in products_enriched. "
             "If omitted, the script queries merchants.products_enriched_<merchant>.",
    )
    p.add_argument("--merchant", default="default")
    p.add_argument("--catalog-sample-size", type=int, default=100)
    p.add_argument(
        "--strategy",
        default="composer_v1",
        help=(
            "Enrichment strategy to sample. Note: each MerchantAgent is scoped to "
            "(merchant_id, strategy); choosing the wrong strategy silently compares "
            "against a different KG projection and will produce misleading coverage."
        ),
    )
    p.add_argument(
        "--fake-llm",
        action="store_true",
        help="Skip the real LLM. Emits a deterministic stub extraction for smoke tests.",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Override output dir (defaults to runs/feature_discovery/<product_type>/<ts>/).",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    out_dir = Path(args.output_dir) if args.output_dir else _default_out_dir(args.product_type)
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("writing artifacts under %s", out_dir)

    # 1. harvest
    queries: list[UserQuery] = []
    for src in sources:
        fetched = harvest(
            src,
            product_type=args.product_type,
            max_queries=args.max_queries - len(queries),
        )
        logger.info("source=%s fetched=%d", src, len(fetched))
        queries.extend(fetched)
        if len(queries) >= args.max_queries:
            break
    if not queries:
        logger.error("no queries harvested; aborting")
        return 2
    _write_jsonl(out_dir / "raw_queries.jsonl", (q.model_dump(mode="json") for q in queries))

    # 2. extract
    extractor = _FakeExtractor() if args.fake_llm else FeatureExtractor()
    extracted: list[ExtractedFeatures] = []
    for i, q in enumerate(queries):
        try:
            extracted.append(extractor.extract(q))
        except Exception as exc:
            logger.warning("extract failed for %s: %s", q.source_id, exc)
        if (i + 1) % 10 == 0:
            logger.info("extracted %d/%d", i + 1, len(queries))
    _write_jsonl(out_dir / "extracted.jsonl", (e.model_dump(mode="json") for e in extracted))

    # 3. aggregate
    freqs = aggregate(extracted)
    (out_dir / "frequencies.json").write_text(
        json.dumps([f.model_dump(mode="json") for f in freqs], indent=2),
        encoding="utf-8",
    )

    # 4. catalog keys + coverage
    catalog_keys, sample_size = _load_catalog_keys(args)
    report = coverage(
        freqs,
        catalog_keys,
        product_type=args.product_type,
        total_queries=len(extracted),
        catalog_sample_size=sample_size,
    )
    (out_dir / "coverage.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8"
    )

    _print_summary(report, freqs)
    return 0


def _default_out_dir(product_type: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _ROOT / "runs" / "feature_discovery" / product_type / ts


def _write_jsonl(path: Path, records) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")


def _load_catalog_keys(args: argparse.Namespace) -> tuple[set[str], int]:
    if args.catalog_keys_file:
        raw = json.loads(Path(args.catalog_keys_file).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return set(raw), len(raw)
        if isinstance(raw, dict) and "keys" in raw:
            return set(raw["keys"]), int(raw.get("sample_size", len(raw["keys"])))
        raise ValueError("catalog-keys-file must be a JSON list[str] or {keys:[...]}")

    # Live mode: pull a sample from products_enriched.
    attrs, sample_size = _sample_enriched_attributes(
        merchant=args.merchant,
        strategy=args.strategy,
        sample_size=args.catalog_sample_size,
    )
    return collect_catalog_keys(attrs), sample_size


def _sample_enriched_attributes(*, merchant: str, strategy: str, sample_size: int):
    """Pull `sample_size` enriched attribute dicts for gap analysis.

    Kept lazy so --fake-llm / --catalog-keys-file runs don't need DB deps.
    """
    # Validate merchant identifier before it is interpolated into raw SQL.
    # The suffix is baked into a table name so parameter binding cannot
    # protect us here; restrict to the same charset the registry uses.
    if not _MERCHANT_ID_RE.match(merchant or ""):
        raise ValueError(
            f"invalid merchant id {merchant!r}; must match {_MERCHANT_ID_RE.pattern}"
        )

    from sqlalchemy import text  # type: ignore

    from app.database import SessionLocal  # type: ignore

    table = f"merchants.products_enriched_{merchant}"
    sql = text(
        f"SELECT attributes FROM {table} WHERE strategy = :strategy LIMIT :n"
    )
    attrs: list[dict] = []
    with SessionLocal() as db:
        rows = db.execute(sql, {"strategy": strategy, "n": sample_size}).fetchall()
    for (row_attrs,) in rows:
        if isinstance(row_attrs, dict):
            attrs.append(row_attrs)
    return attrs, len(attrs)


def _print_summary(report, freqs) -> None:
    logger.info(
        "corpus: %d queries, catalog sample: %d rows",
        report.total_queries,
        report.catalog_sample_size,
    )
    top_missing = sorted(report.missing, key=lambda f: -f.count)[:10]
    top_covered = sorted(report.covered, key=lambda f: -f.count)[:5]
    logger.info("top covered features:")
    for f in top_covered:
        logger.info("  [%s] %-32s count=%d frac=%.2f", f.bucket, f.key, f.count, f.fraction)
    logger.info("top missing features (candidates to extract):")
    for f in top_missing:
        logger.info("  [%s] %-32s count=%d frac=%.2f", f.bucket, f.key, f.count, f.fraction)
    logger.info("underused catalog keys (%d):", len(report.underused))
    for k in report.underused[:10]:
        logger.info("  %s", k)


@dataclass
class _FakeExtractor:
    """Deterministic stub used for offline smoke tests.

    Produces a reasonable-looking extraction from keyword hits in the
    query text so the rest of the pipeline has something to aggregate
    without hitting OpenAI.
    """

    def extract(self, query: UserQuery) -> ExtractedFeatures:
        text_lower = query.text.lower()
        rules: list[tuple[str, str, str]] = [
            # (keyword, bucket, emitted_key)
            ("battery", "mentioned_attributes", "battery_life_hours"),
            ("ram", "mentioned_attributes", "ram_gb"),
            ("ssd", "mentioned_attributes", "storage_type_ssd"),
            ("nvme", "mentioned_attributes", "storage_type_nvme"),
            ("weight", "mentioned_attributes", "weight_kg"),
            ("lbs", "mentioned_attributes", "weight_kg"),
            ("oled", "mentioned_attributes", "display_panel_oled"),
            ("refresh", "mentioned_attributes", "refresh_rate_hz"),
            ("matte", "mentioned_attributes", "screen_finish_matte"),
            ("fan", "implicit_concerns", "fan_noise"),
            ("hot", "implicit_concerns", "runs_hot"),
            ("silent", "implicit_concerns", "fan_noise"),
            ("linux", "implicit_concerns", "linux_compatibility"),
            ("repair", "implicit_concerns", "repairability"),
            ("rugged", "implicit_concerns", "durability"),
            ("build", "implicit_concerns", "build_quality"),
            ("speaker", "mentioned_attributes", "speaker_quality"),
            ("webcam", "mentioned_attributes", "webcam_quality"),
            ("gaming", "use_cases", "gaming"),
            ("video editing", "use_cases", "video_editing"),
            ("college", "use_cases", "college"),
            ("dev", "use_cases", "software_dev"),
            ("photoshop", "use_cases", "photo_editing"),
            ("streaming", "use_cases", "streaming"),
            ("zoom", "use_cases", "video_calls"),
            ("field", "use_cases", "fieldwork"),
            ("budget", "hard_constraints", "budget"),
            ("$", "hard_constraints", "budget"),
            ("thunderbolt", "hard_constraints", "has_thunderbolt"),
            ("ethernet", "hard_constraints", "has_ethernet"),
            ("pen", "hard_constraints", "pen_support"),
            ("tpm", "hard_constraints", "has_tpm"),
            ("brand", "soft_preferences", "brand"),
            ("thinkpad", "soft_preferences", "brand_thinkpad"),
            ("framework", "soft_preferences", "brand_framework"),
            ("mac", "soft_preferences", "brand_apple"),
            ("rgb", "soft_preferences", "no_rgb"),
        ]
        buckets: dict[str, list[str]] = {
            "mentioned_attributes": [],
            "use_cases": [],
            "hard_constraints": [],
            "soft_preferences": [],
            "implicit_concerns": [],
        }
        for keyword, bucket, emit in rules:
            if keyword in text_lower and emit not in buckets[bucket]:
                buckets[bucket].append(emit)
        return ExtractedFeatures(
            source_id=query.source_id,
            product_type=query.product_type,
            mentioned_attributes=buckets["mentioned_attributes"],
            use_cases=buckets["use_cases"],
            hard_constraints=buckets["hard_constraints"],
            soft_preferences=buckets["soft_preferences"],
            implicit_concerns=buckets["implicit_concerns"],
            model="fake-llm",
        )


if __name__ == "__main__":
    raise SystemExit(main())
