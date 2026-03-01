"""Pytest configuration for MCP server tests."""

import os
import pytest


# ---------------------------------------------------------------------------
# Cart isolation — _CARTS is a module-level dict in app.endpoints that
# persists across test modules within a single pytest session.  Clear it
# before AND after every test so state from one test never bleeds into the
# next, regardless of execution order.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function", autouse=True)
def _isolate_carts():
    """Wipe in-memory cart store before and after every test."""
    try:
        from app.endpoints import _CARTS  # noqa: PLC0415
        _CARTS.clear()
    except ImportError:
        pass
    yield
    try:
        from app.endpoints import _CARTS  # noqa: PLC0415
        _CARTS.clear()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Postgres availability check (runs once at collection time)
# ---------------------------------------------------------------------------

_DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://julih@localhost:5432/mcp_ecommerce"


def _postgres_available() -> bool:
    """Return True if a real Postgres server is reachable."""
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(_DATABASE_URL, connect_args={"connect_timeout": 3})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


_POSTGRES_UP = _postgres_available()

# Test files that require a live Postgres connection.
# When Postgres is unavailable (e.g. CI without the secret set) these are
# skipped automatically — same pattern as Redis tests use pytest.skip().
_POSTGRES_REQUIRED_FILES = {
    "test_database.py",
    "test_endpoint_integration.py",
    "test_endpoints.py",
    "test_mcp_pipeline.py",
    "test_reddit_queries.py",
    "test_week6_enriched.py",
}


def pytest_collection_modifyitems(items, config):
    """Auto-skip Postgres-dependent tests when the DB is not reachable."""
    if _POSTGRES_UP:
        return  # Postgres is up — run everything normally

    skip_marker = pytest.mark.skip(
        reason="Postgres not available — set DATABASE_URL secret to run DB tests"
    )
    for item in items:
        if getattr(item.fspath, "basename", "") in _POSTGRES_REQUIRED_FILES:
            item.add_marker(skip_marker)
