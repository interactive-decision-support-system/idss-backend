"""
Issue #53 — persistent merchant registry.

Locks in the post-migration contract for ``merchants.registry``:

  1. Hydration from the registry works with the constructor alone — no
     enrichment, no vector rebuild. Clearing ``merchant_agent.main.merchants`` and
     calling ``/merchant/{id}/*`` must still serve the merchant.

  2. Cross-worker parity: a row written in one Postgres session is visible
     to a fresh, cache-cold lookup in another session (same-process
     simulation, since the durability contract lives in Postgres not in
     Python).

  3. The default merchant is bootstrapped idempotently (cold / warm / twice).

  4. ``upsert_registry_row`` — the helper ``MerchantAgent.from_csv`` calls
     next to the in-memory dict write — is itself idempotent: a second
     call with the same ``merchant_id`` updates strategy fields and bumps
     ``updated_at`` instead of erroring.

All tests are Postgres-integration tests and are auto-skipped via
``conftest.py`` when ``DATABASE_URL`` is not reachable.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env before importing app so DATABASE_URL is available.
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from merchant_agent import main as app_main
from merchant_agent.database import DATABASE_URL
from merchant_agent.merchant_agent import MerchantAgent, upsert_registry_row


_TEST_DATABASE_URL = os.getenv("DATABASE_URL") or DATABASE_URL
_engine = create_engine(_TEST_DATABASE_URL, pool_pre_ping=True)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _require_migration_005() -> None:
    """Skip the whole module if migration 005 has not been applied yet."""
    with _engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT to_regclass('merchants.registry') IS NOT NULL"
            )
        ).scalar()
    if not exists:
        pytest.skip(
            "merchants.registry not present — apply migration 005 before "
            "running these tests."
        )


@pytest.fixture
def db_session():
    s = _Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def synthetic_merchant_ids():
    """Return a list of test-only merchant_ids and scrub them before + after.

    Provisions per-merchant catalog tables (``merchants.products_<id>`` +
    ``merchants.products_enriched_<id>``) before yielding and drops them
    after. The hydrate path now probes Postgres via ``open_catalog`` — a
    registry row with no backing table raises ``CatalogNotFound``, which
    these tests are not exercising. Bootstrapping the tables keeps the
    fixture realistic: a merchant the registry knows about should also
    have its DDL in place.

    The slug grammar (MERCHANT_ID_RE) forbids hyphens, so we use underscores.
    """
    from merchant_agent.ingestion.schema import create_merchant_catalog, drop_merchant_catalog

    ids = ["test_reg_hydrate", "test_reg_worker", "test_reg_idemp"]

    def _scrub_registry():
        with _engine.begin() as conn:
            conn.execute(
                text("DELETE FROM merchants.registry WHERE merchant_id = ANY(:ids)"),
                {"ids": ids},
            )

    def _drop_tables():
        raw_conn = _engine.raw_connection()
        try:
            for mid in ids:
                try:
                    drop_merchant_catalog(mid, raw_conn, _force=True)
                except Exception:
                    pass
        finally:
            raw_conn.close()

    _scrub_registry()
    _drop_tables()

    raw_conn = _engine.raw_connection()
    try:
        for mid in ids:
            create_merchant_catalog(mid, raw_conn)
    finally:
        raw_conn.close()

    yield ids

    _scrub_registry()
    _drop_tables()


@pytest.fixture
def clean_dict_cache():
    """Snapshot ``merchant_agent.main.merchants`` before and restore after the test."""
    snapshot = dict(app_main.merchants)
    try:
        yield
    finally:
        app_main.merchants.clear()
        app_main.merchants.update(snapshot)


# ---------------------------------------------------------------------------
# 1. Restart simulation — lazy hydrate, no rebuild
# ---------------------------------------------------------------------------


def test_hydrate_from_registry_after_dict_clear(
    db_session, synthetic_merchant_ids, clean_dict_cache, monkeypatch
):
    """Cache miss + registry hit must construct the agent and cache it.

    Locks the "restart simulation" contract: ``merchant_agent.main.merchants`` empty
    (simulates a fresh worker process), ``merchants.registry`` populated
    (simulates the persistent source of truth), lookup must return the
    agent without triggering enrichment or vector rebuild.
    """
    mid = synthetic_merchant_ids[0]

    # Seed the registry directly — no from_csv, so we prove the hydrate path
    # does not depend on any per-test catalog bootstrap.
    with _engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO merchants.registry
                    (merchant_id, domain, strategy, kg_strategy)
                VALUES (:mid, 'books', 'normalizer_v1', 'default_v1')
                """
            ),
            {"mid": mid},
        )

    # Clear the in-process cache so the helper must hydrate from DB.
    app_main.merchants.clear()

    # Trip-wire: if anything in this code path attempts to re-enrich or
    # rebuild the vector index, fail the test. Plain hydration must only
    # call the MerchantAgent constructor.
    def _fail_vector(self):  # pragma: no cover - trip wire
        pytest.fail("refresh_vector_index called during hydrate")

    def _fail_enrich(*a, **kw):  # pragma: no cover - trip wire
        pytest.fail("CatalogNormalizer.batch_normalize called during hydrate")

    monkeypatch.setattr(MerchantAgent, "refresh_vector_index", _fail_vector)
    from merchant_agent.catalog_ingestion import CatalogNormalizer
    monkeypatch.setattr(CatalogNormalizer, "batch_normalize", _fail_enrich)

    agent = app_main._get_or_hydrate_merchant(mid, db_session)

    assert isinstance(agent, MerchantAgent)
    assert agent.merchant_id == mid
    assert agent.domain == "books"
    assert agent.strategy == "normalizer_v1"
    assert agent.kg_strategy == "default_v1"
    # And the dict now caches it, so a second call is a pure cache hit.
    assert app_main.merchants[mid] is agent


def test_hydrate_unknown_merchant_returns_404(db_session, clean_dict_cache):
    """A cache miss that also misses the registry must raise HTTPException 404."""
    from fastapi import HTTPException

    app_main.merchants.clear()

    with pytest.raises(HTTPException) as excinfo:
        app_main._get_or_hydrate_merchant("ghost_merchant_for_issue_53", db_session)
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# 2. Multi-worker simulation
# ---------------------------------------------------------------------------


def test_cross_session_visibility(
    db_session, synthetic_merchant_ids, clean_dict_cache
):
    """Row written in session A is visible to cache-cold hydrate in session B.

    A uvicorn --workers=N deployment has N independent Python processes;
    each has its own ``merchant_agent.main.merchants`` dict but shares Postgres. This
    test simulates that by writing via one SQLAlchemy session, clearing
    the dict, and reading through ``_get_or_hydrate_merchant`` with a
    second session — the exact path worker B would take for a merchant
    first registered on worker A.
    """
    mid = synthetic_merchant_ids[1]

    # "Worker A" — register via the same helper from_csv uses.
    upsert_registry_row(
        merchant_id=mid,
        domain="apparel",
        strategy="normalizer_v2",
        kg_strategy="alt_kg_v1",
    )

    # Start worker B with an empty cache and a brand-new session.
    app_main.merchants.clear()
    session_b = _Session()
    try:
        agent = app_main._get_or_hydrate_merchant(mid, session_b)
    finally:
        session_b.close()

    assert agent.merchant_id == mid
    assert agent.domain == "apparel"
    # Both strategy axes survive the round-trip — this is the specific
    # regression #53 has to block.
    assert agent.strategy == "normalizer_v2"
    assert agent.kg_strategy == "alt_kg_v1"


# ---------------------------------------------------------------------------
# 3. Default merchant bootstrap — cold + warm + idempotent
# ---------------------------------------------------------------------------


def test_default_registry_row_is_idempotent(db_session):
    """Running the bootstrap UPSERT twice yields exactly one row.

    Mirrors the lifespan block in ``merchant_agent.main`` that issues
    ``INSERT ... ON CONFLICT DO NOTHING`` for the default merchant on every
    startup. A second startup must not duplicate the row nor error.
    """
    bootstrap = text(
        """
        INSERT INTO merchants.registry
            (merchant_id, domain, strategy, kg_strategy)
        VALUES ('default', 'electronics', 'normalizer_v1', 'default_v1')
        ON CONFLICT (merchant_id) DO NOTHING
        """
    )
    with _engine.begin() as conn:
        conn.execute(bootstrap)
        conn.execute(bootstrap)

    count = db_session.execute(
        text("SELECT COUNT(*) FROM merchants.registry WHERE merchant_id = 'default'")
    ).scalar()
    assert count == 1


def test_default_merchant_hydrates_cold(db_session, clean_dict_cache):
    """With the dict empty, the default merchant must hydrate from the registry.

    Covers the worker-B-sees-default scenario: the lifespan block already
    ran on worker A and seeded both the dict and the registry; worker B's
    dict starts empty and the first /merchant/default/* request must still
    work. Prerequisite: the lifespan on this test DB has seeded the row
    at least once (either by running the app, or by the previous test in
    this module).
    """
    # Ensure the default row exists regardless of test-ordering — we don't
    # want this test to depend on test_default_registry_row_is_idempotent
    # running first.
    with _engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO merchants.registry
                    (merchant_id, domain, strategy, kg_strategy)
                VALUES ('default', 'electronics', 'normalizer_v1', 'default_v1')
                ON CONFLICT (merchant_id) DO NOTHING
                """
            )
        )

    app_main.merchants.clear()
    agent = app_main._get_or_hydrate_merchant("default", db_session)
    assert agent.merchant_id == "default"
    assert agent.domain == "electronics"
    assert agent.strategy == "normalizer_v1"
    assert agent.kg_strategy == "default_v1"


# ---------------------------------------------------------------------------
# 4. upsert_registry_row idempotency
# ---------------------------------------------------------------------------


def test_upsert_registry_row_is_idempotent(
    db_session, synthetic_merchant_ids
):
    """Two calls with the same merchant_id — one row, updated strategy fields,
    updated_at advanced.

    ``MerchantAgent.from_csv`` calls this helper on every invocation; the
    registry UPSERT is the only idempotency guarantee in this PR. This
    test asserts the UPSERT behaviour directly rather than running the
    full CSV pipeline — CSV row-level dedupe is explicitly out of scope
    for #53.
    """
    mid = synthetic_merchant_ids[2]

    upsert_registry_row(
        merchant_id=mid,
        domain="pets",
        strategy="normalizer_v1",
        kg_strategy="default_v1",
    )
    first = db_session.execute(
        text(
            "SELECT domain, strategy, kg_strategy, created_at, updated_at "
            "FROM merchants.registry WHERE merchant_id = :mid"
        ),
        {"mid": mid},
    ).mappings().first()
    assert first is not None
    assert first["domain"] == "pets"
    assert first["strategy"] == "normalizer_v1"

    # Postgres ``now()`` has microsecond resolution but the two UPSERTs can
    # still land in the same microsecond on a fast machine; force a gap.
    time.sleep(0.01)

    # Second call with different strategy values.
    upsert_registry_row(
        merchant_id=mid,
        domain="pets_supplies",
        strategy="normalizer_v2",
        kg_strategy="alt_kg_v1",
    )

    # Fresh read — SessionLocal's earlier statement sits in a separate txn.
    db_session.commit()
    second = db_session.execute(
        text(
            "SELECT domain, strategy, kg_strategy, created_at, updated_at "
            "FROM merchants.registry WHERE merchant_id = :mid"
        ),
        {"mid": mid},
    ).mappings().first()

    # Still exactly one row.
    count = db_session.execute(
        text("SELECT COUNT(*) FROM merchants.registry WHERE merchant_id = :mid"),
        {"mid": mid},
    ).scalar()
    assert count == 1

    # Values refreshed to the most recent call.
    assert second["domain"] == "pets_supplies"
    assert second["strategy"] == "normalizer_v2"
    assert second["kg_strategy"] == "alt_kg_v1"

    # created_at preserved, updated_at advanced.
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] > first["updated_at"]
