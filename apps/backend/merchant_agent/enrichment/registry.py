"""Strategy registry + disjoint-keys invariant.

Every enrichment strategy registers its OUTPUT_KEYS at module-import time.
The registry rejects:
  - overlap with raw products.attributes keys (KNOWN_RAW_ATTRIBUTE_KEYS)
  - overlap between any two registered strategies

This keeps enriched_reader.combine_raw_and_enriched safe by construction.
"""

from __future__ import annotations

from typing import Type

# ---------------------------------------------------------------------------
# Known raw-attribute keys
# ---------------------------------------------------------------------------
# Union of:
#   - JSONB keys observed in seed data and read by merchant_agent.models.Product properties
#   - keys CatalogNormalizer / csv_importer pull out via _SPEC_KEYS
#   - slots referenced by agent/domain_registry.py
# Anything an enrichment strategy emits MUST NOT appear here.

KNOWN_RAW_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        # Surfaced by merchant_agent.models.Product properties
        "description",
        "color",
        "gpu_vendor",
        "gpu_model",
        "tags",
        "reviews",
        "kg_features",
        # Spec keys CatalogNormalizer pulls from JSONB
        "ram_gb",
        "storage_gb",
        "processor",
        "cpu",
        "gpu",
        "display_size",
        "screen_size",
        "battery_life",
        "os",
        "operating_system",
        "resolution",
        "refresh_rate",
        "weight_kg",
        "weight_lbs",
        "author",
        "genre",
        "pages",
        "year",
        "engine",
        "mileage",
        "fuel_type",
        "transmission",
        # Domain-registry slot keys (laptops, cameras)
        "ram_gb",
        "storage_type",
        "battery_life_hours",
        "refresh_rate_hz",
        "megapixels",
        "sensor_type",
        "lens_mount",
        "video_resolution",
        "image_stabilization",
        "weather_sealed",
        "burst_fps",
        "body_style",
    }
)


# Strategies whose key footprints are known up front. Includes:
#   - strategies implemented outside this module (normalizer_v1 lives in
#     catalog_ingestion.py)
#   - strategies in this module that don't follow the per-product
#     BaseEnrichmentAgent contract (validator_v1 audit row)
# These survive _reset_for_tests() so tests don't depend on import order.
EXTERNAL_STRATEGY_KEYS: dict[str, frozenset[str]] = {
    "normalizer_v1": frozenset({"normalized_description", "normalized_at"}),
    "validator_v1": frozenset({"validated_strategies", "validated_at"}),
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class StrategyKeyCollision(ValueError):
    """Raised when a registered strategy violates the disjoint-keys invariant."""


_REGISTRY: dict[str, Type] = {}
_REGISTERED_KEYS: dict[str, frozenset[str]] = dict(EXTERNAL_STRATEGY_KEYS)
# Union of every registered agent's NARRATIVE_KEYS. Composer reads this to
# strip narrative output from the canonical row without hard-coding which
# agent owns which key (issue #83 review, item 4).
_NARRATIVE_KEYS: set[str] = set()


def register(agent_cls: Type) -> Type:
    """Decorator: register a BaseEnrichmentAgent subclass.

    The class must define class-level STRATEGY (str) and OUTPUT_KEYS (frozenset[str]).
    Raises StrategyKeyCollision on overlap with raw or another strategy.
    """
    strategy = getattr(agent_cls, "STRATEGY", None)
    output_keys = getattr(agent_cls, "OUTPUT_KEYS", None)
    if not strategy or not isinstance(strategy, str):
        raise StrategyKeyCollision(f"{agent_cls.__name__} must define STRATEGY: str")
    if not isinstance(output_keys, (set, frozenset)) or not output_keys:
        raise StrategyKeyCollision(
            f"{agent_cls.__name__} must define non-empty OUTPUT_KEYS: frozenset[str]"
        )

    keys = frozenset(output_keys)
    raw_overlap = keys & KNOWN_RAW_ATTRIBUTE_KEYS
    if raw_overlap:
        raise StrategyKeyCollision(
            f"strategy {strategy!r} OUTPUT_KEYS overlap raw attributes: {sorted(raw_overlap)}"
        )

    for other_strategy, other_keys in _REGISTERED_KEYS.items():
        if other_strategy == strategy:
            continue
        cross = keys & other_keys
        if cross:
            raise StrategyKeyCollision(
                f"strategy {strategy!r} OUTPUT_KEYS overlap {other_strategy!r}: {sorted(cross)}"
            )

    narrative = getattr(agent_cls, "NARRATIVE_KEYS", frozenset()) or frozenset()
    # narrative keys must be a subset of output keys — otherwise the agent is
    # claiming to own a key it doesn't emit.
    narrative_outside_output = set(narrative) - set(keys)
    if narrative_outside_output:
        raise StrategyKeyCollision(
            f"strategy {strategy!r} NARRATIVE_KEYS must be a subset of OUTPUT_KEYS; "
            f"extra keys: {sorted(narrative_outside_output)}"
        )

    _REGISTRY[strategy] = agent_cls
    _REGISTERED_KEYS[strategy] = keys
    _NARRATIVE_KEYS.update(narrative)
    return agent_cls


def register_external(strategy: str, output_keys: frozenset[str]) -> None:
    """Claim a key footprint without registering an agent class.

    For strategies that don't follow the per-product BaseEnrichmentAgent
    contract (validator audit row, normalizer_v1 lives elsewhere) but still
    need to participate in the disjoint-keys check.
    """
    if not isinstance(output_keys, (set, frozenset)) or not output_keys:
        raise StrategyKeyCollision(
            f"register_external({strategy!r}) requires non-empty OUTPUT_KEYS"
        )
    keys = frozenset(output_keys)
    raw_overlap = keys & KNOWN_RAW_ATTRIBUTE_KEYS
    if raw_overlap:
        raise StrategyKeyCollision(
            f"strategy {strategy!r} OUTPUT_KEYS overlap raw attributes: {sorted(raw_overlap)}"
        )
    for other_strategy, other_keys in _REGISTERED_KEYS.items():
        if other_strategy == strategy:
            continue
        cross = keys & other_keys
        if cross:
            raise StrategyKeyCollision(
                f"strategy {strategy!r} OUTPUT_KEYS overlap {other_strategy!r}: {sorted(cross)}"
            )
    _REGISTERED_KEYS[strategy] = keys


def get(strategy: str) -> Type:
    if strategy not in _REGISTRY:
        raise KeyError(f"strategy {strategy!r} not registered")
    return _REGISTRY[strategy]


def list_strategies() -> list[str]:
    """Names of all in-module registered strategies (excludes EXTERNAL_STRATEGY_KEYS)."""
    return sorted(_REGISTRY.keys())


def output_keys(strategy: str) -> frozenset[str]:
    return _REGISTERED_KEYS[strategy]


def all_known_keys() -> dict[str, frozenset[str]]:
    """Snapshot of every key footprint the registry currently knows about,
    including external strategies. Useful for the MerchantAgentClient."""
    return dict(_REGISTERED_KEYS)


def narrative_keys() -> frozenset[str]:
    """Union of every registered agent's self-declared NARRATIVE_KEYS. The
    composer strips these from the canonical row — see ComposerAgent."""
    return frozenset(_NARRATIVE_KEYS)


def _reset_for_tests() -> None:
    """Test helper — wipes in-module strategies, keeps EXTERNAL_STRATEGY_KEYS."""
    _REGISTRY.clear()
    _REGISTERED_KEYS.clear()
    _REGISTERED_KEYS.update(EXTERNAL_STRATEGY_KEYS)
    _NARRATIVE_KEYS.clear()
