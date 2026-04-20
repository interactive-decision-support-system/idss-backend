"""Aggregate extracted features across a corpus and diff against catalog.

Two steps:

  aggregate(features) -> FeatureFrequency rows per bucket.
  coverage(freqs, catalog_keys) -> CoverageReport splitting discovered
                                   features into covered vs missing,
                                   plus underused catalog keys that no
                                   shopper talked about.

The "catalog keys" input is a set[str] of attribute keys the existing
enrichment pipeline currently emits. In live mode the CLI
(``scripts/mine_user_queries.py::_sample_enriched_attributes``) selects
``attributes`` from ``merchants.products_enriched_<merchant>`` filtered
by ``strategy``, then ``collect_catalog_keys`` flattens each row's
``attributes`` dict (one level of nesting) and unions the keys.
Offline, the CLI loads the same shape from a sample JSON.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from app.feature_discovery.types import (
    CoverageReport,
    ExtractedFeatures,
    FeatureFrequency,
)


_BUCKETS: tuple[tuple[str, str], ...] = (
    ("mentioned_attributes", "mentioned_attribute"),
    ("use_cases", "use_case"),
    ("hard_constraints", "hard_constraint"),
    ("soft_preferences", "soft_preference"),
    ("implicit_concerns", "implicit_concern"),
)


def aggregate(features: Iterable[ExtractedFeatures]) -> list[FeatureFrequency]:
    """Return per-bucket frequencies sorted by count descending."""
    features = list(features)
    total = max(1, len(features))
    counters: dict[str, Counter[str]] = {b: Counter() for _, b in _BUCKETS}
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)

    for feat in features:
        for attr, bucket_label in _BUCKETS:
            for key in getattr(feat, attr):
                counters[bucket_label][key] += 1
                ex = examples[(bucket_label, key)]
                if feat.source_id not in ex and len(ex) < 3:
                    ex.append(feat.source_id)

    out: list[FeatureFrequency] = []
    for _, bucket in _BUCKETS:
        for key, count in counters[bucket].most_common():
            out.append(
                FeatureFrequency(
                    key=key,
                    bucket=bucket,  # type: ignore[arg-type]
                    count=count,
                    fraction=count / total,
                    example_source_ids=examples[(bucket, key)],
                )
            )
    return out


def coverage(
    freqs: list[FeatureFrequency],
    catalog_keys: set[str],
    *,
    product_type: str,
    total_queries: int,
    catalog_sample_size: int,
    min_fraction: float = 0.0,
) -> CoverageReport:
    """Split discovered features into covered / missing vs catalog keys.

    Covered: the shopper key (or a close variant) exists in catalog_keys.
    Missing: frequent shopper key with no catalog representation.
    Underused: catalog key that no shopper in the corpus mentioned.

    "Close variant" uses an axis-anchored one-way subset check. Split on
    `_`; the shopper key's FIRST token (the axis, e.g. 'battery' in
    'battery_life_hours') must appear in the catalog key's token set,
    AND every shopper token must be present in the catalog tokens. This
    covers unit-suffix drift (shopper 'battery_life' vs catalog
    'battery_life_hours') without collapsing value specialization into
    the axis itself (shopper 'brand_apple' must NOT match catalog
    'brand', since 'apple' is absent from the catalog tokens).
    """
    covered: list[FeatureFrequency] = []
    missing: list[FeatureFrequency] = []

    norm_catalog = {k: set(k.split("_")) for k in catalog_keys}

    for freq in freqs:
        if freq.fraction < min_fraction:
            continue
        if _catalog_match(freq.key, norm_catalog):
            covered.append(freq)
        else:
            missing.append(freq)

    shopper_token_lists = [f.key.split("_") for f in freqs]
    underused: list[str] = []
    for k in sorted(catalog_keys):
        k_tokens = set(k.split("_"))
        used = False
        for s_tokens in shopper_token_lists:
            if not s_tokens:
                continue
            s_set = set(s_tokens)
            if s_tokens[0] in k_tokens and s_set.issubset(k_tokens):
                used = True
                break
        if not used:
            underused.append(k)

    return CoverageReport(
        product_type=product_type,
        total_queries=total_queries,
        catalog_sample_size=catalog_sample_size,
        covered=covered,
        missing=missing,
        underused=underused,
    )


def _catalog_match(shopper_key: str, norm_catalog: dict[str, set[str]]) -> bool:
    # Axis-anchored one-way subset: the shopper key's first token (the
    # axis) must appear in the catalog key's tokens, AND the full shopper
    # token set must be a subset of the catalog tokens. Preserves exact
    # matches (identical token sets satisfy both conditions) while
    # rejecting cases where the shopper specialized a catalog axis with
    # a value token the catalog doesn't carry (e.g. 'brand_apple' vs
    # catalog 'brand').
    shopper_tokens = shopper_key.split("_")
    if not shopper_tokens or not shopper_tokens[0]:
        return False
    shopper_set = set(shopper_tokens)
    axis = shopper_tokens[0]
    for _, tokens in norm_catalog.items():
        if axis in tokens and shopper_set.issubset(tokens):
            return True
    return False


def collect_catalog_keys(enriched_attribute_dicts: Iterable[dict]) -> set[str]:
    """Flatten a bag of products_enriched.attributes dicts into a key set.

    Nested dicts (e.g. ``good_for_tags: {good_for_gaming: 0.8, ...}``)
    are flattened one level so both the parent and leaf keys participate
    in the match, matching how ``composer_v1`` eventually surfaces them
    as canonical columns.
    """
    out: set[str] = set()
    for attrs in enriched_attribute_dicts:
        if not isinstance(attrs, dict):
            continue
        for key, value in attrs.items():
            out.add(key)
            if isinstance(value, dict):
                for sub in value.keys():
                    if isinstance(sub, str):
                        out.add(sub)
    return out
