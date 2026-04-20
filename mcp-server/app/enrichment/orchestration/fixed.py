"""FixedOrchestrator: runs every assessor-recommended strategy on every product."""

from __future__ import annotations

from app.enrichment.types import AssessorOutput, OrchestratorPlan, ProductInput


class FixedOrchestrator:
    """Plan = the assessor's recommended_strategies, applied to every product."""

    def plan(self, products: list[ProductInput], assessment: AssessorOutput) -> OrchestratorPlan:
        ordered = _ordered_strategies(assessment.recommended_strategies)
        return OrchestratorPlan(
            per_product_agents={p.product_id: list(ordered) for p in products}
        )


# Strategy run order matters: taxonomy first (others read its product_type),
# then parser (extracts specs the specialist consults), then specialist /
# scraper / soft_tagger. composer_v1 runs last — it is the single writer of
# the canonical row and synthesizes all upstream findings (issue #83).
_PREFERRED_ORDER = (
    "taxonomy_v1",
    "parser_v1",
    "specialist_v1",
    "scraper_v1",
    "soft_tagger_v1",
    "composer_v1",
)


def _ordered_strategies(recommended: list[str]) -> list[str]:
    rec = set(recommended)
    return [s for s in _PREFERRED_ORDER if s in rec]
