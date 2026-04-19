"""
Catalog binding — unit + integration tests for ``app.catalog`` and the
related ``MerchantAgent`` constructor refactor.

Three contracts get pinned here:

1. ``Catalog.for_merchant`` builds the right table names and models from
   the slug alone — no DB round-trip. This is the path the lifespan
   bootstrap and ``from_csv`` rely on, and we don't want a DB probe sneaking
   into it.

2. ``open_catalog`` probes Postgres and raises ``CatalogNotFound`` when the
   raw table is missing. The test for the *missing* case is a pure unit
   test (no DB) — we monkey-patch the session's ``execute`` so it can run
   in environments without Postgres. The *present* case is a Postgres
   integration test, gated by ``conftest.py``'s skip marker.

3. ``MerchantAgent.__init__`` rejects a Catalog whose merchant_id disagrees
   with the agent's — this is the only way a caller can wire the wrong
   tenant's tables into a fresh agent, so it has to fail loudly.

Plus one regression test: ``MerchantAgent.from_csv`` must NOT install the
agent into ``app.main.merchants`` — that's now the route's job (issue #69).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

# Load .env before importing app modules so DATABASE_URL is set.
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# 1. Catalog.for_merchant — pure unit
# ---------------------------------------------------------------------------


def test_for_merchant_derives_names_and_models_without_db():
    """Slug → fully-qualified table names + per-merchant ORM classes.

    No DB connection is created; ``for_merchant`` is a pure derivation. This
    is the contract the lifespan bootstrap and ``from_csv`` rely on (both
    run before the catalog tables exist or are about to be created).
    """
    from app.catalog import Catalog

    cat = Catalog.for_merchant("acme_books")

    assert cat.merchant_id == "acme_books"
    assert cat.raw_table == "merchants.products_acme_books"
    assert cat.enriched_table == "merchants.products_enriched_acme_books"
    # The factory binds the ORM class to the per-merchant table name.
    assert cat.product_model.__tablename__ == "products_acme_books"
    assert cat.enriched_model.__tablename__ == "products_enriched_acme_books"


def test_for_merchant_validates_slug():
    """Invalid slugs must raise — defence against SQL injection at the ORM seam."""
    from app.catalog import Catalog

    with pytest.raises(ValueError):
        Catalog.for_merchant("Bad-Slug!")  # uppercase, hyphen, bang — all rejected


def test_for_merchant_is_frozen():
    """Catalog is an immutable value — accidental rebinding must raise."""
    from app.catalog import Catalog
    from dataclasses import FrozenInstanceError

    cat = Catalog.for_merchant("default")
    with pytest.raises(FrozenInstanceError):
        cat.merchant_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. open_catalog — missing table case (pure unit, no DB)
# ---------------------------------------------------------------------------


def test_open_catalog_raises_when_table_missing():
    """``to_regclass`` returns NULL → CatalogNotFound, with merchant_id and table."""
    from app.catalog import CatalogNotFound, open_catalog

    fake_db = MagicMock()
    # Simulate Postgres saying the relation does not exist.
    fake_db.execute.return_value.scalar.return_value = None

    with pytest.raises(CatalogNotFound) as excinfo:
        open_catalog("ghost_merchant", fake_db)

    assert excinfo.value.merchant_id == "ghost_merchant"
    assert excinfo.value.missing_table == "merchants.products_ghost_merchant"


def test_open_catalog_returns_catalog_when_table_present():
    """``to_regclass`` returns a non-NULL OID → Catalog binds successfully."""
    from app.catalog import Catalog, open_catalog

    fake_db = MagicMock()
    # Postgres returns the regclass OID — any truthy value satisfies the probe.
    fake_db.execute.return_value.scalar.return_value = "merchants.products_acme"

    cat = open_catalog("acme", fake_db)
    assert isinstance(cat, Catalog)
    assert cat.merchant_id == "acme"
    assert cat.raw_table == "merchants.products_acme"


# ---------------------------------------------------------------------------
# 3. MerchantAgent constructor — Catalog wiring
# ---------------------------------------------------------------------------


def test_agent_default_catalog_matches_for_merchant():
    """``MerchantAgent(mid, ...)`` builds an unverified Catalog from the slug.

    No DB probe — same contract as ``for_merchant``. The agent's table
    accessors must agree with the standalone Catalog factory.
    """
    from app.catalog import Catalog
    from app.merchant_agent import MerchantAgent

    agent = MerchantAgent(merchant_id="default", domain="electronics")

    assert agent.catalog == Catalog.for_merchant("default")
    assert agent.catalog_table() == "merchants.products_default"
    assert agent.enriched_table() == "merchants.products_enriched_default"


def test_agent_accepts_explicit_catalog():
    """Passing ``catalog=`` overrides the slug-derived default.

    Used by ``MerchantAgent.open(...)`` to inject a verified Catalog without
    re-deriving it. The agent reads through to the catalog's models.
    """
    from app.catalog import Catalog
    from app.merchant_agent import MerchantAgent

    cat = Catalog.for_merchant("acme")
    agent = MerchantAgent(merchant_id="acme", domain="apparel", catalog=cat)

    assert agent.catalog is cat
    assert agent.product_model is cat.product_model


def test_agent_rejects_mismatched_catalog():
    """A catalog whose merchant_id disagrees with the agent must raise.

    Without this guard, a caller could splice another tenant's tables into
    a fresh agent and silently write into the wrong catalog.
    """
    from app.catalog import Catalog
    from app.merchant_agent import MerchantAgent

    other_cat = Catalog.for_merchant("acme")
    with pytest.raises(ValueError, match="does not match"):
        MerchantAgent(
            merchant_id="default",
            domain="electronics",
            catalog=other_cat,
        )


# ---------------------------------------------------------------------------
# 4. Issue #69 — from_csv must not self-register
# ---------------------------------------------------------------------------


def test_from_csv_no_longer_writes_app_main_merchants(monkeypatch, tmp_path):
    """``from_csv`` returns the agent *without* installing it into the cache.

    The route handler now owns the cache write. A scripted/offline call
    site that doesn't want its agent live-served must not have to fight
    this side effect.

    We stub every external dependency ``from_csv`` reaches (DDL, CSV load,
    enrichment, vector index, registry UPSERT) so the test is pure: it only
    asserts the post-condition on ``app.main.merchants``.
    """
    from app import main as app_main
    from app.merchant_agent import MerchantAgent

    csv_path = tmp_path / "tiny.csv"
    csv_path.write_text("title,brand\nWidget,Acme\n")

    mid = "iss69_no_register"

    # Snapshot + restore the cache so other tests aren't affected by the
    # explicit pre-state we set up below.
    snapshot = dict(app_main.merchants)
    app_main.merchants.pop(mid, None)

    # Stub every collaborator from_csv pulls in. The point of the test is
    # the absence of one assignment, not the pipeline shape.
    from app.ingestion import schema as schema_mod
    from app.ingestion import csv_loader as loader_mod
    from app import catalog_ingestion as enrich_mod

    monkeypatch.setattr(schema_mod, "create_merchant_catalog", lambda *a, **kw: None)
    monkeypatch.setattr(
        loader_mod,
        "load_csv_into_merchant",
        lambda *a, **kw: {"loaded": 0, "skipped": 0},
    )
    monkeypatch.setattr(
        enrich_mod.CatalogNormalizer,
        "batch_normalize",
        lambda self, *a, **kw: {"normalized": 0},
    )
    monkeypatch.setattr(
        MerchantAgent, "refresh_vector_index", lambda self: 0
    )
    # Registry UPSERT also touches the DB — stub it.
    import app.merchant_agent as ma_mod
    monkeypatch.setattr(ma_mod, "upsert_registry_row", lambda **kw: None)

    # SessionLocal is called twice (raw load + enrichment); a stand-in
    # context-manager-friendly object is enough — none of the collaborators
    # actually use it, since they're all stubbed.
    class _StubSession:
        def get_bind(self):
            class _Engine:
                def raw_connection(self):
                    class _RawConn:
                        def close(self): pass
                    return _RawConn()
            return _Engine()
        def close(self): pass

    monkeypatch.setattr(
        "app.database.SessionLocal",
        lambda: _StubSession(),
    )

    try:
        agent = MerchantAgent.from_csv(
            str(csv_path),
            merchant_id=mid,
            domain="widgets",
            product_type="widget",
            skip_enrichment=True,
        )

        assert agent.merchant_id == mid
        # The contract: from_csv returns the agent but the in-process cache
        # remains untouched. POST /merchant is responsible for installing it.
        assert mid not in app_main.merchants, (
            "from_csv must not write app.main.merchants — that's the "
            "route's job after issue #69"
        )
    finally:
        app_main.merchants.clear()
        app_main.merchants.update(snapshot)


# ---------------------------------------------------------------------------
# 5. SupabaseProductStore REST guard
# ---------------------------------------------------------------------------


def test_rest_store_rejects_non_default_merchant_id():
    """Non-default merchant_id on the REST path raises NotImplementedError.

    REST hits ``/rest/v1/products`` (legacy ``public.products``) — it has no
    notion of ``merchants.products_<id>``. Routing a non-default merchant
    here would silently leak the default catalog's rows. The guard fails
    loudly so the operator configures DATABASE_URL.
    """
    from app.tools.supabase_product_store import _reject_non_default_merchant

    # Default and missing merchant_id: no-op.
    _reject_non_default_merchant({"merchant_id": "default"}, "search_products")
    _reject_non_default_merchant({}, "search_products")
    _reject_non_default_merchant(None, "search_products")

    # Non-default: raise.
    with pytest.raises(NotImplementedError, match="cannot serve merchant_id"):
        _reject_non_default_merchant(
            {"merchant_id": "acme_books"}, "search_products"
        )
