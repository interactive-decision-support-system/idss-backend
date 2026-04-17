"""Enrichment agents.

Importing this package side-effect-registers every agent class with
app.enrichment.registry. Order of imports matters only because the disjoint-
keys check runs at import time — putting registrations together here keeps
collisions visible.
"""

from app.enrichment.agents import (  # noqa: F401 - import side effects
    assessor,
    parser,
    soft_tagger,
    specialist,
    taxonomy,
    validator,
    web_scraper,
)
