"""
Issue #54 — HTTP admin surface for merchants.

Locks in the public contract for the three new routes:

  * ``POST /merchant``    — multipart CSV upload, sync provisioning.
  * ``GET  /merchant``    — list rows from ``merchants.registry``.
  * ``DELETE /merchant/{id}`` — drop tables + registry row + dict cache.

These tests are Postgres-integration tests (auto-skipped via
``conftest.py`` when DATABASE_URL is unreachable) and require migration
005 (``merchants.registry``) plus 002/003 (``products_default`` template
tables) to be present.

Enrichment and the per-merchant FAISS index are stubbed for the duration
of every test that POSTs a merchant — both call out to OpenAI / sentence
transformers, neither is in scope for the admin-routes contract, and the
search assertion only needs SQL-level dispatch to confirm rows are
sourced from the new merchant table.

KG is also out of scope for #54 (tracked in #61). Search via the new
merchant will fall back to ``MerchantAgent``'s positional ``score``
ranking — these tests deliberately avoid asserting on
``score_breakdown``.
"""

from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import main as app_main
from app.database import DATABASE_URL
from app.main import app


_TEST_DATABASE_URL = os.getenv("DATABASE_URL") or DATABASE_URL
_engine = create_engine(_TEST_DATABASE_URL, pool_pre_ping=True)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _require_migration_005() -> None:
    """Skip the module if merchants.registry isn't present."""
    with _engine.connect() as conn:
        exists = conn.execute(
            text("SELECT to_regclass('merchants.registry') IS NOT NULL")
        ).scalar()
    if not exists:
        pytest.skip(
            "merchants.registry not present — apply migration 005 before "
            "running these tests."
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _scrub_merchant(merchant_id: str) -> None:
    """Force-drop a test merchant's tables and registry row.

    Idempotent — used both as setup (in case a prior crashed run left
    state) and teardown.
    """
    os.environ["ALLOW_MERCHANT_DROP"] = "1"
    from app.ingestion.schema import drop_merchant_catalog

    raw_conn = _engine.raw_connection()
    try:
        try:
            drop_merchant_catalog(merchant_id, raw_conn)
        except Exception:
            # Tables may not exist yet on the first call; ignore.
            pass
    finally:
        raw_conn.close()
    with _engine.begin() as conn:
        conn.execute(
            text("DELETE FROM merchants.registry WHERE merchant_id = :mid"),
            {"mid": merchant_id},
        )
    app_main.merchants.pop(merchant_id, None)


@pytest.fixture
def temp_merchant_ids():
    """A pool of deterministic test ids; scrub before + after every test."""
    ids = [
        "test_admin_one",
        "test_admin_two",
        "test_admin_three",
    ]
    for mid in ids:
        _scrub_merchant(mid)
    yield ids
    for mid in ids:
        _scrub_merchant(mid)


@pytest.fixture
def stub_enrichment_and_vector(monkeypatch):
    """Skip OpenAI + sentence-transformer work for admin-route tests.

    Enrichment correctness is covered by the catalog_ingestion tests; the
    per-merchant FAISS index is covered by test_per_merchant_vector_index.
    Neither belongs in the admin-routes contract — and exercising them
    here would make the test suite depend on OPENAI_API_KEY and a
    several-second model load on every run.
    """
    from app.catalog_ingestion import CatalogNormalizer
    from app.merchant_agent import MerchantAgent

    monkeypatch.setattr(
        CatalogNormalizer,
        "batch_normalize",
        lambda self, *a, **kw: {"normalized": 0, "skipped": 0, "failed": 0},
    )
    monkeypatch.setattr(MerchantAgent, "refresh_vector_index", lambda self: 0)


def _csv_bytes(rows: list[dict]) -> bytes:
    """Build an in-memory CSV from a list of dicts."""
    if not rows:
        return b"title,price,product_type\n"
    headers = list(rows[0].keys())
    buf = io.StringIO()
    buf.write(",".join(headers) + "\n")
    for r in rows:
        buf.write(",".join(str(r[h]) for h in headers) + "\n")
    return buf.getvalue().encode("utf-8")


def _post_merchant(client: TestClient, *, merchant_id: str, **overrides):
    """POST /merchant helper. ``overrides`` patches form fields and CSV rows."""
    rows = overrides.pop("rows", None) or [
        {"title": "Test Book One", "price": "9.99", "product_type": "book"},
        {"title": "Test Book Two", "price": "12.50", "product_type": "book"},
    ]
    data = {
        "merchant_id": merchant_id,
        "domain": overrides.pop("domain", "books"),
        "product_type": overrides.pop("product_type", "book"),
    }
    for k in ("strategy", "kg_strategy", "col_map"):
        if k in overrides:
            data[k] = overrides.pop(k)
    files = {"file": ("catalog.csv", _csv_bytes(rows), "text/csv")}
    return client.post("/merchant", data=data, files=files)


# ---------------------------------------------------------------------------
# POST /merchant
# ---------------------------------------------------------------------------


def test_post_creates_merchant_and_search_returns_uploaded_rows(
    client, temp_merchant_ids, stub_enrichment_and_vector
):
    """End-to-end: POST a CSV, then /merchant/{id}/search returns its rows.

    Asserts that ``Offer.product.product_id`` is one of the IDs that
    landed in ``merchants.products_<id>`` from this POST — proves the
    new merchant agent is dispatched (not the default catalog).
    Score semantics are out of scope here.
    """
    mid = temp_merchant_ids[0]

    resp = _post_merchant(client, merchant_id=mid)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["merchant_id"] == mid
    assert body["onboarding_state"] == "ready"
    assert body["catalog_size"] == 2

    # Snapshot the IDs that actually landed in the per-merchant table —
    # cannot pre-compute them because csv_loader assigns UUIDs at insert.
    db = _Session()
    try:
        # The Postgres column is 'id'; the Python ORM attribute is 'product_id'.
        rows = db.execute(
            text(f"SELECT id FROM merchants.products_{mid}")
        ).all()
    finally:
        db.close()
    posted_ids = {str(r[0]) for r in rows}
    assert len(posted_ids) == 2

    search_resp = client.post(
        f"/merchant/{mid}/search",
        json={"domain": "books", "top_k": 5},
    )
    assert search_resp.status_code == 200, search_resp.text
    offers = search_resp.json()
    assert offers, "search returned no offers — merchant agent not dispatched"
    returned_ids = {o["product"]["product_id"] for o in offers}
    assert returned_ids & posted_ids, (
        f"none of the returned offers are from this merchant's catalog. "
        f"returned={returned_ids} posted={posted_ids}"
    )


def test_post_invalid_merchant_id_returns_400(
    client, stub_enrichment_and_vector
):
    """Slug grammar is enforced at the boundary, before the upload is read."""
    resp = _post_merchant(client, merchant_id="BadID-with-hyphen")
    assert resp.status_code == 400, resp.text
    assert "merchant_id" in resp.json()["detail"]


def test_post_duplicate_merchant_id_returns_409_and_leaves_existing_intact(
    client, temp_merchant_ids, stub_enrichment_and_vector
):
    """Re-POST is rejected without touching the existing rows.

    The CSV load path is append-only; a silent re-POST would duplicate
    every row in ``merchants.products_<id>``. The 409 is the only thing
    standing between an operator's typo and a doubled catalog.
    """
    mid = temp_merchant_ids[1]

    # First POST seeds the merchant.
    first = _post_merchant(client, merchant_id=mid)
    assert first.status_code == 201

    db = _Session()
    try:
        before_count = db.execute(
            text(f"SELECT COUNT(*) FROM merchants.products_{mid}")
        ).scalar()
        before_registry = db.execute(
            text(
                "SELECT domain, strategy, kg_strategy "
                "FROM merchants.registry WHERE merchant_id = :mid"
            ),
            {"mid": mid},
        ).mappings().first()
    finally:
        db.close()

    # Second POST with the same id, deliberately different payload — the
    # 409 must fire before any write, so even the registry row stays put.
    dup = _post_merchant(
        client,
        merchant_id=mid,
        domain="apparel",
        rows=[{"title": "Should Not Land", "price": "1.00", "product_type": "book"}],
    )
    assert dup.status_code == 409, dup.text
    assert mid in dup.json()["detail"]

    db = _Session()
    try:
        after_count = db.execute(
            text(f"SELECT COUNT(*) FROM merchants.products_{mid}")
        ).scalar()
        after_registry = db.execute(
            text(
                "SELECT domain, strategy, kg_strategy "
                "FROM merchants.registry WHERE merchant_id = :mid"
            ),
            {"mid": mid},
        ).mappings().first()
    finally:
        db.close()
    assert after_count == before_count, (
        f"duplicate POST mutated catalog: {before_count} -> {after_count}"
    )
    assert dict(after_registry) == dict(before_registry), (
        "duplicate POST mutated registry row"
    )


def test_post_persists_explicit_kg_strategy(
    client, temp_merchant_ids, stub_enrichment_and_vector
):
    """The kg_strategy form field is threaded into the registry UPSERT."""
    mid = temp_merchant_ids[2]

    resp = _post_merchant(
        client, merchant_id=mid, kg_strategy="alt_kg_v1"
    )
    assert resp.status_code == 201, resp.text

    with _engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT kg_strategy FROM merchants.registry "
                "WHERE merchant_id = :mid"
            ),
            {"mid": mid},
        ).mappings().first()
    assert row is not None
    assert row["kg_strategy"] == "alt_kg_v1"


# ---------------------------------------------------------------------------
# GET /merchant
# ---------------------------------------------------------------------------


def test_get_lists_default_and_new_merchant_with_all_fields(
    client, temp_merchant_ids, stub_enrichment_and_vector
):
    """GET returns one entry per registry row with the full six-field shape.

    ``catalog_size`` must reflect the live COUNT(*) from the per-merchant
    table — the registry doesn't cache it, so the value drifts the moment
    rows are inserted or deleted.
    """
    mid = temp_merchant_ids[0]

    create = _post_merchant(client, merchant_id=mid)
    assert create.status_code == 201

    resp = client.get("/merchant")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    by_id = {e["merchant_id"]: e for e in entries}

    assert "default" in by_id, "default merchant must always be listed"

    new = by_id.get(mid)
    assert new is not None, f"newly POSTed merchant {mid} not in GET output"
    assert set(new.keys()) == {
        "merchant_id", "domain", "strategy",
        "kg_strategy", "catalog_size", "created_at",
    }
    assert new["domain"] == "books"
    assert new["catalog_size"] == 2  # matches the two CSV rows


# ---------------------------------------------------------------------------
# DELETE /merchant/{merchant_id}
# ---------------------------------------------------------------------------


def test_delete_without_env_var_returns_403(
    client, temp_merchant_ids, stub_enrichment_and_vector, monkeypatch
):
    """Without ``ALLOW_MERCHANT_DROP=1`` the helper raises and we surface 403."""
    mid = temp_merchant_ids[0]

    create = _post_merchant(client, merchant_id=mid)
    assert create.status_code == 201

    # Deliberately pop the env var the underlying helper guards on.
    monkeypatch.delenv("ALLOW_MERCHANT_DROP", raising=False)

    resp = client.delete(f"/merchant/{mid}")
    assert resp.status_code == 403, resp.text

    # Registry row still present — DELETE was a no-op.
    with _engine.connect() as conn:
        still_there = conn.execute(
            text(
                "SELECT 1 FROM merchants.registry WHERE merchant_id = :mid"
            ),
            {"mid": mid},
        ).first()
    assert still_there is not None


def test_delete_with_env_var_removes_registry_row_dict_and_get_listing(
    client, temp_merchant_ids, stub_enrichment_and_vector, monkeypatch
):
    """The happy path — DELETE drops tables, registry row, and the cache entry."""
    mid = temp_merchant_ids[1]

    create = _post_merchant(client, merchant_id=mid)
    assert create.status_code == 201
    # POST /merchant installs the cache entry after from_csv returns.
    # (Pre-#69, from_csv did the write itself; the route now owns it.)
    assert mid in app_main.merchants

    monkeypatch.setenv("ALLOW_MERCHANT_DROP", "1")

    resp = client.delete(f"/merchant/{mid}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merchant_id": mid, "deleted": True}

    # Registry row gone.
    with _engine.connect() as conn:
        gone = conn.execute(
            text(
                "SELECT 1 FROM merchants.registry WHERE merchant_id = :mid"
            ),
            {"mid": mid},
        ).first()
    assert gone is None

    # Dict entry gone.
    assert mid not in app_main.merchants

    # GET no longer lists it.
    listing = client.get("/merchant").json()
    assert mid not in {e["merchant_id"] for e in listing}


def test_default_is_listable_like_any_other_merchant(
    client, monkeypatch
):
    """Since migration 006, 'default' has no structural privilege.

    The previous version of this test asserted that DELETE /merchant/default
    returned 400 even with ALLOW_MERCHANT_DROP=1 — that guard existed because
    the default merchant was the clone template for new-merchant provisioning.
    After migration 006 the clone template moved to merchants.raw_products_default
    (the archive), so 'default' is dropable like any other merchant (gated
    solely on ALLOW_MERCHANT_DROP).

    We do NOT actually exercise DELETE /merchant/default here — that would
    wipe the default merchant mid-test-run and break every other test that
    depends on its catalog existing. The structural symmetry is verified by
    test_delete_with_env_var_removes_registry_row_dict_and_get_listing via
    a temp merchant; what this test confirms is that 'default' appears in
    GET /merchant on the same footing as any other registered merchant.
    """
    resp = client.get("/merchant")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    by_id = {e["merchant_id"]: e for e in entries}
    default = by_id.get("default")
    assert default is not None, "'default' should be a regular merchant in the registry"
    # Shape is identical to any other merchant's entry.
    assert set(default.keys()) == {
        "merchant_id", "domain", "strategy",
        "kg_strategy", "catalog_size", "created_at",
    }
