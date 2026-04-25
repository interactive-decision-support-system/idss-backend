"""Multi-agent catalog enrichment.

Each agent produces typed output written to merchants.products_enriched_default
under its own strategy label. The disjoint-keys rule (enriched_reader.combine_*)
is enforced at module-import time by registry.py — every strategy declares its
OUTPUT_KEYS up front and registration fails on overlap.
"""
