import uuid
from agent.interview.session_manager import InterviewSessionManager


def _fresh_sid(label: str) -> str:
    """Generate a unique session ID so Redis-cached state from prior runs can't bleed in."""
    return f"{label}-{uuid.uuid4().hex[:8]}"


def test_session_manager_basic():
    sm = InterviewSessionManager()
    assert sm is not None


# ---------------------------------------------------------------------------
# excluded_brands must EXTEND across turns, not replace
# ---------------------------------------------------------------------------

def test_excluded_brands_accumulate_across_turns():
    """
    Turn 1: user says "no HP and no Acer" → excluded_brands = ["HP", "Acer"]
    Turn 2: user says "also no Dell"       → should become ["HP", "Acer", "Dell"]
    BUG (fixed): update_filters was doing dict-assign which overwrote the list.
    """
    sm = InterviewSessionManager()
    sid = _fresh_sid("excl-accum")

    # Turn 1 — fresh session, set two exclusions
    sm.update_filters(sid, {"excluded_brands": ["HP", "Acer"]})
    after_t1 = list(sm.get_session(sid).explicit_filters["excluded_brands"])
    assert "HP" in after_t1, f"HP not set after turn 1: {after_t1}"
    assert "Acer" in after_t1, f"Acer not set after turn 1: {after_t1}"

    # Turn 2 — must EXTEND, not replace
    sm.update_filters(sid, {"excluded_brands": ["Dell"]})
    result = sm.get_session(sid).explicit_filters["excluded_brands"]
    assert "HP" in result, f"HP was dropped — exclusion list was overwritten: {result}"
    assert "Acer" in result, f"Acer was dropped — exclusion list was overwritten: {result}"
    assert "Dell" in result, f"Dell was not added: {result}"
    assert len(result) == 3, f"Expected 3 items, got {len(result)}: {result}"


def test_excluded_brands_no_duplicates():
    """Re-stating the same exclusion should not duplicate it."""
    sm = InterviewSessionManager()
    sid = _fresh_sid("excl-dedup")
    sm.update_filters(sid, {"excluded_brands": ["HP"]})
    sm.update_filters(sid, {"excluded_brands": ["HP", "Dell"]})
    result = sm.get_session(sid).explicit_filters["excluded_brands"]
    assert result.count("HP") == 1, f"HP appears more than once: {result}"
    assert "Dell" in result


def test_excluded_brands_first_set_is_list():
    """If excluded_brands hasn't been set yet, the first value is stored as-is."""
    sm = InterviewSessionManager()
    sid = _fresh_sid("excl-first")
    sm.update_filters(sid, {"excluded_brands": ["Apple"]})
    result = sm.get_session(sid).explicit_filters["excluded_brands"]
    assert "Apple" in result


def test_other_slots_still_replace():
    """Non-exclusion slots like budget must still be replaced, not accumulated."""
    sm = InterviewSessionManager()
    sid = _fresh_sid("replace")
    sm.update_filters(sid, {"price_max_cents": 80000})
    sm.update_filters(sid, {"price_max_cents": 60000})
    assert sm.get_session(sid).explicit_filters["price_max_cents"] == 60000


# ---------------------------------------------------------------------------
# replace=True must fully replace explicit_filters
# ---------------------------------------------------------------------------

def test_update_filters_replace_drops_stale_keys():
    """
    With replace=True, stale keys from a previous turn must be gone.
    Scenario: turn 1 sets good_for_gaming, turn 2 replaces with a budget-only
    filter set — good_for_gaming should not survive.
    """
    sm = InterviewSessionManager()
    sid = _fresh_sid("replace-mode")

    sm.update_filters(sid, {"good_for_gaming": True, "min_ram_gb": 16})
    assert sm.get_session(sid).explicit_filters.get("good_for_gaming") is True

    # Replace with a completely new filter set
    sm.update_filters(sid, {"price_max_cents": 80000}, replace=True)
    ef = sm.get_session(sid).explicit_filters
    assert ef.get("price_max_cents") == 80000
    assert "good_for_gaming" not in ef, "stale key survived replace=True"
    assert "min_ram_gb" not in ef, "stale key survived replace=True"
