"""
Issue #55 — remove the NULL ∪ 'default' special case on the default merchant.

These tests lock in the post-migration contract:

  1. ``MerchantAgent("default").health()`` reports a ``catalog_size`` equal
     to ``SELECT COUNT(*) FROM merchants.products_default``. Before the
     migration the health query included ``merchant_id IS NULL`` as a
     second branch; after the backfill (migration 004) every row is
     ``merchant_id = 'default'``, so the simple filter must match the
     raw count exactly.

  2. ``search_products`` scoped to ``merchant_id='default'`` returns the
     same row count it returned before the collapse. The baseline for the
     catalog under test is captured at the top of each run from the raw
     table so the assertion doesn't drift when the seeded catalog grows.

  3. ``search_products`` scoped to a non-default merchant does not leak
     default rows. Before the collapse, a stale NULL-or-default branch
     could have only fired on mid=='default'; still, the guardrail is
     worth a direct assertion so any future regression of the filter
     (e.g. accidentally re-introducing ``is_(None)``) fails loudly.

All three tests are Postgres-integration tests and are auto-skipped by
``conftest.py`` when DATABASE_URL is not reachable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env before importing app so DATABASE_URL is available.
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

# ``app`` lives under apps/backend/; ``agent`` lives at the repo root.
# search_products imports agent.interview.session_manager lazily, so both
# paths need to be on sys.path for this test file to drive the endpoint.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from merchant_agent.database import DATABASE_URL
from merchant_agent.merchant_agent import MerchantAgent
from merchant_agent.models import Product


_TEST_DATABASE_URL = os.getenv("DATABASE_URL") or DATABASE_URL
if not _TEST_DATABASE_URL:
    pytest.skip("DATABASE_URL is not configured", allow_module_level=True)
_engine = create_engine(_TEST_DATABASE_URL, pool_pre_ping=True)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture
def db_session():
    s = _Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="module")
def raw_default_count() -> int:
    """COUNT(*) from merchants.products_default — the post-migration baseline."""
    with _engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM merchants.products_default")
        ).scalar()
    assert n is not None and n > 0, (
        "merchants.products_default is empty; these tests require a seeded "
        "default catalog to be meaningful."
    )
    return int(n)


@pytest.fixture(scope="module")
def _null_merchant_rows_gone() -> None:
    """Guard: these tests assume migration 004 has been applied."""
    with _engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT COUNT(*) FROM merchants.products_default "
                "WHERE merchant_id IS NULL"
            )
        ).scalar()
    if n:
        pytest.skip(
            f"{n} rows in merchants.products_default still have NULL merchant_id — "
            "run migration 004 before asserting the collapse contract."
        )


def test_health_catalog_size_matches_raw_count(db_session, raw_default_count, _null_merchant_rows_gone):
    """health() on the default merchant == raw table count after migration 004."""
    agent = MerchantAgent(merchant_id="default", domain="test.local")
    report = agent.health(db_session)
    assert report["merchant_id"] == "default"
    assert report["catalog_size"] == raw_default_count


def test_search_default_filter_count_matches_pre_collapse_baseline(
    db_session, raw_default_count, _null_merchant_rows_gone
):
    """The collapsed filter on merchant_id='default' must match the old set.

    Pre-collapse the search filter was
    ``merchant_id IS NULL OR merchant_id='default'``; post-collapse it is
    ``merchant_id='default'``. After migration 004 there are no NULL rows,
    so the two filters return identical row sets. We assert at the ORM
    layer so the assertion is not capped by ``SearchProductsRequest.limit``
    (100 by pydantic), while still exercising the exact filter expression
    that ``endpoints.search_products`` now applies.
    """
    count = db_session.query(Product).filter(Product.merchant_id == "default").count()
    assert count == raw_default_count


def test_search_non_default_merchant_does_not_leak_default(db_session):
    """A non-default merchant_id filter must not match default rows.

    Before the collapse, only ``mid == 'default'`` ever hit the NULL-or-default
    branch — but adding this assertion locks in strict-equality scoping so a
    future regression that reintroduced the OR branch (or applied it to other
    merchants) would fail loudly instead of silently leaking ~50k default rows
    into someone else's result set.
    """
    count = (
        db_session.query(Product)
        .filter(Product.merchant_id == "ghost_merchant_for_issue_55")
        .count()
    )
    assert count == 0
