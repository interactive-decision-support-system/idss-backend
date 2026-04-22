"""
Unit tests for check_explainability() in scripts/run_geval.py.

check_explainability() scores 0-1 via three binary sub-checks:
  c1 — bullet marker OR explanatory connective present
  c2 — at least one product attribute keyword (ram, price, battery, etc.)
  c3 — at least one constraint/filter disclosure phrase
score = (c1 + c2 + c3) / 3.0

Returns (None, "N/A...") when q["expect_explain"] is absent/False.
"""
import sys
import os

# Repo root must be on sys.path; conftest.py adds it at session start.
# This explicit insert handles running the file directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.run_geval import check_explainability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(expect_explain=True):
    """Minimal query dict with expect_explain flag."""
    return {
        "id": 999,
        "group": "test",
        "label": "test query",
        "message": "recommend a laptop",
        "expect_recs_on_first": True,
        "expect_explain": expect_explain,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "",
    }


def _resp(message=""):
    """Minimal response dict."""
    return {
        "message": message,
        "response_type": "recommendations",
        "recommendations": [],
    }


# ---------------------------------------------------------------------------
# Full pass (all 3 checks) → 1.0
# ---------------------------------------------------------------------------

def test_full_explanation_scores_1():
    """All three checks pass: bullets + attribute + constraint."""
    msg = (
        "Here are laptops under $1000 matching your needs:\n"
        "- Dell XPS 15: 16GB RAM and fast SSD storage\n"
        "- Asus ROG: rating 4.8 stars and good battery"
    )
    score, note = check_explainability(_q(), _resp(msg))
    assert score == 1.0, f"Expected 1.0, got {score}. Note: {note}"
    assert "all 3" in note


# ---------------------------------------------------------------------------
# Full fail (no checks pass) → 0.0
# ---------------------------------------------------------------------------

def test_no_explanation_scores_0():
    """No bullets, no attributes, no constraint phrases."""
    msg = "Here are some options for you: Model A, Model B, Model C."
    score, note = check_explainability(_q(), _resp(msg))
    assert score == 0.0, f"Expected 0.0, got {score}. Note: {note}"
    assert "missing" in note


# ---------------------------------------------------------------------------
# One check: only bullets → 0.33
# ---------------------------------------------------------------------------

def test_only_bullets_scores_one_third():
    """Check 1 passes (dash bullet), checks 2 and 3 fail."""
    msg = "Here are some laptops:\n- Option A\n- Option B"
    score, note = check_explainability(_q(), _resp(msg))
    expected = round(1 / 3.0, 4)
    assert abs(score - expected) < 0.001, f"Expected ~0.333, got {score}"
    assert "bullets" in note


# ---------------------------------------------------------------------------
# Two checks: bullets + attribute, no constraint → 0.67
# ---------------------------------------------------------------------------

def test_bullets_and_attr_scores_two_thirds():
    """Checks 1 and 2 pass (bullets + ram), check 3 fails."""
    msg = "Here are some laptops:\n- Option A with 16GB RAM\n- Option B with SSD"
    score, note = check_explainability(_q(), _resp(msg))
    expected = round(2 / 3.0, 4)
    assert abs(score - expected) < 0.001, f"Expected ~0.667, got {score}"


# ---------------------------------------------------------------------------
# N/A when expect_explain is absent
# ---------------------------------------------------------------------------

def test_na_when_expect_explain_absent():
    """No expect_explain key → returns (None, 'N/A...')."""
    q = _q(expect_explain=True)
    del q["expect_explain"]
    score, note = check_explainability(q, _resp("anything with bullets - and ram"))
    assert score is None
    assert "N/A" in note


# ---------------------------------------------------------------------------
# N/A when expect_explain is False
# ---------------------------------------------------------------------------

def test_na_when_expect_explain_false():
    """expect_explain=False → returns (None, 'N/A...')."""
    score, note = check_explainability(_q(expect_explain=False), _resp("bullets - ram under $"))
    assert score is None
    assert "N/A" in note


# ---------------------------------------------------------------------------
# Numbered list passes check 1 (bullet check)
# ---------------------------------------------------------------------------

def test_numbered_list_passes_bullet_check():
    """Numbered list item triggers c1 via regex."""
    msg = "1. Dell XPS: great battery and 16GB RAM under $1000\n2. Lenovo IdeaPad: SSD storage"
    score, note = check_explainability(_q(), _resp(msg))
    assert score == 1.0, f"Expected 1.0 (numbered list + RAM + under $), got {score}"


# ---------------------------------------------------------------------------
# Connective word alone passes check 1
# ---------------------------------------------------------------------------

def test_connective_word_passes_bullet_check():
    """'because' triggers c1 even without bullet markers."""
    msg = "We recommend this laptop because it has good price and battery life."
    score, note = check_explainability(_q(), _resp(msg))
    # c1=1 (because), c2=1 (price + battery), c3=0 (no constraint phrase) → 0.67
    expected = round(2 / 3.0, 4)
    assert abs(score - expected) < 0.001, f"Expected ~0.667, got {score}"


# ---------------------------------------------------------------------------
# Constraint phrase alone passes check 3
# ---------------------------------------------------------------------------

def test_constraint_phrase_alone_passes_check3():
    """'under $' triggers c3 even without bullets or attributes."""
    msg = "We found laptops under $800 for you."
    score, note = check_explainability(_q(), _resp(msg))
    # c1=0, c2=0, c3=1 → 0.33
    expected = round(1 / 3.0, 4)
    assert abs(score - expected) < 0.001, f"Expected ~0.333, got {score}"
