"""Unit tests for ``scripts/enrichment_inspector.py`` pure functions.

The inspector is a Streamlit script and most of its surface is rendering,
but ``kg_property_catalog()`` is a plain function that composes the KG
property reference table the dashboard renders. We test it directly so
that if ``kg_projection.TAG_CONFIDENCE_THRESHOLD`` (#60) ever moves, the
inspector's user-visible note moves with it instead of silently lying.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load the script as a module. It lives under scripts/, not in the app
# package, so a normal import won't find it. We do this once per session.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "enrichment_inspector.py"


@pytest.fixture(scope="module")
def inspector_module():
    if not _SCRIPT_PATH.exists():
        pytest.skip(f"inspector script not found at {_SCRIPT_PATH}")

    spec = importlib.util.spec_from_file_location(
        "enrichment_inspector_under_test", _SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        # The script transitively pulls in the full mcp-server import chain
        # (streamlit, sqlalchemy, redis via app.endpoints, …). Skip when any
        # of those are missing instead of failing — this test only cares
        # about kg_property_catalog(), which is pure.
        pytest.skip(f"inspector script can't be imported in this env: {exc}")
    return module


def test_kg_property_catalog_soft_tagger_row_carries_live_threshold(
    inspector_module,
):
    """If the projection's TAG_CONFIDENCE_THRESHOLD changes, the inspector
    note must change with it — no stale 0.5 baked into the dashboard."""
    from app import kg_projection

    rows = inspector_module.kg_property_catalog()
    soft_rows = [r for r in rows if r["producer"] == "pattern:soft_tagger_v1"]
    assert soft_rows, "expected at least one soft_tagger_v1 KEY_PATTERNS row"

    expected_threshold = float(kg_projection.TAG_CONFIDENCE_THRESHOLD)
    for row in soft_rows:
        note = row["notes"]
        assert "Cypher" in note and "#60" in note, (
            f"soft-tagger note should anchor to #60: {note!r}"
        )
        assert str(expected_threshold) in note, (
            "soft-tagger note must carry the live "
            f"TAG_CONFIDENCE_THRESHOLD ({expected_threshold}); got: {note!r}"
        )


def test_kg_property_catalog_only_soft_tagger_rows_get_threshold_note(
    inspector_module,
):
    """Identity / flattening / reserved rows must not inherit the soft-tag
    note — that gating is the whole point of the producer-strategy check."""
    rows = inspector_module.kg_property_catalog()
    for row in rows:
        if row["producer"] == "pattern:soft_tagger_v1":
            continue
        assert row["notes"] == "", (
            f"non-soft-tagger row leaked the threshold note: {row!r}"
        )
