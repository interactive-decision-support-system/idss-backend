#!/usr/bin/env python3
"""
DEPRECATED — retired under issue #52.

This script used to regex-scan ``products.name`` / ``products.description``
for use-case hints (good_for_gaming, good_for_ml, battery_life_hours, …)
and write them back into ``products.attributes.kg_features``. Two problems
the #52 contract rewrite surfaces:

1. It writes to ``products``, which is now the immutable golden catalog —
   no INSERT/UPDATE allowed without explicit user approval.
2. The direction is backward. The KG contract is
   ``products_enriched → KG``; enrichment lives in ``app.enrichment`` and
   writes per-strategy rows to ``products_enriched`` keyed by
   ``(merchant_id, strategy)``.

Whether any of the original regex/source-heuristic logic should migrate
into the pipeline (e.g. as a cheap rule-based ``heuristic_v1`` strategy
that runs before LLM enrichment) is tracked in issue #61.

Shim intentionally prints + exits 1 so any remaining caller surfaces this
retirement in its logs instead of silently no-op-ing.
"""

import sys


_MESSAGE = (
    "backfill_kg_features.py has been retired under issue #52.\n"
    "\n"
    "Enrichment now runs via app.enrichment (taxonomy_v1 → parser_v1 →\n"
    "specialist_v1 → soft_tagger_v1) and writes to products_enriched\n"
    "under per-strategy rows. The KG builder consumes those rows via\n"
    "app.kg_projection; the direction is one-way (enriched → KG).\n"
    "\n"
    "If you need the old script's output, see issue #61 for the decision\n"
    "on whether the regex/source-heuristic logic migrates into the pipeline.\n"
)


def main() -> int:
    sys.stderr.write(_MESSAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
