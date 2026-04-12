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
# excluded_brands must survive an unrelated filter update (Q1 regression guard)
# ---------------------------------------------------------------------------

def test_excluded_brands_survive_unrelated_filter_update():
    """
    Adding a non-exclusion filter (e.g. min_ram_gb) must NOT clear excluded_brands.

    Regression: if update_filters() ever incorrectly replaces the whole dict,
    previously accumulated exclusions would silently vanish.
    """
    sm = InterviewSessionManager()
    sid = _fresh_sid("survive-update")

    # Turn 1: user excludes HP
    sm.update_filters(sid, {"excluded_brands": ["HP"]})

    # Turn 2: user adds a RAM requirement — exclusion must survive
    sm.update_filters(sid, {"min_ram_gb": 16})

    result = sm.get_session(sid).explicit_filters
    assert "HP" in result.get("excluded_brands", []), (
        f"HP exclusion was lost after unrelated filter update. Filters: {result}"
    )
    assert result.get("min_ram_gb") == 16, "min_ram_gb was not set"


def test_price_downgrade_clears_stale_price_key():
    """
    Q4 Scenario A: user states $800 budget (T1) then changes to $600 (T2).

    The session must NOT end up with both price_max_cents=80000 AND price_max_cents=60000
    (impossible, it's the same key) OR budget="under$800" AND price_max_cents=60000
    (different keys, both active — the $800 key would make the search wrong).

    This test covers the cross-key scenario:
    - T1 stores {budget: "under$800"} (agent internal format)
    - T2 refinement stores {price_max_cents: 60000} (search format)
    - Both must NOT coexist — otherwise the old "$800" can bleed into a later search.
    """
    sm = InterviewSessionManager()
    sid = _fresh_sid("price-downgrade")

    # T1: agent internal representation (budget as string)
    sm.update_filters(sid, {"budget": "under$800"})

    # T2: refinement converts budget → price_max_cents (search-compatible format)
    # Uses replace=True as process_refinement() does in chat_endpoint.py:2953
    sm.update_filters(sid, {"price_max_cents": 60000}, replace=True)

    sess = sm.get_session(sid)
    filters = sess.explicit_filters

    # The stale "budget" key must be gone — replace=True should have cleared it
    assert "budget" not in filters, (
        f"Stale 'budget' key survived replace=True update. Filters: {filters}"
    )
    # The new price must be correct (not the old $800 = 80000 cents)
    assert filters.get("price_max_cents") == 60000, (
        f"Expected price_max_cents=60000, got {filters.get('price_max_cents')}. Filters: {filters}"
    )


def test_price_same_key_overwrite():
    """
    Simplest case: same key (price_max_cents) written twice — second must win.
    This is the non-replace merge path used in most update_filters calls.
    """
    sm = InterviewSessionManager()
    sid = _fresh_sid("price-same-key")
    sm.update_filters(sid, {"price_max_cents": 80000})
    sm.update_filters(sid, {"price_max_cents": 60000})
    result = sm.get_session(sid).explicit_filters["price_max_cents"]
    assert result == 60000, f"Expected 60000 after overwrite, got {result}"


def test_excluded_brands_accumulate_string_and_list_inputs():
    """
    _merge_excluded_brands must handle both string ('HP,Dell') and list (['HP', 'Dell'])
    inputs on successive turns, deduplicating correctly.
    """
    sm = InterviewSessionManager()
    sid = _fresh_sid("string-list")

    # First update with a list
    sm.update_filters(sid, {"excluded_brands": ["HP", "Acer"]})

    # Second update with a comma-separated string (as LLM sometimes produces)
    sm.update_filters(sid, {"excluded_brands": "Dell,HP"})

    result = sm.get_session(sid).explicit_filters.get("excluded_brands", [])
    # Normalise to list for comparison
    if isinstance(result, str):
        result = [b.strip() for b in result.split(",") if b.strip()]

    assert "HP" in result, f"HP missing: {result}"
    assert "Acer" in result, f"Acer missing: {result}"
    assert "Dell" in result, f"Dell missing: {result}"
    assert result.count("HP") == 1, f"HP duplicated: {result}"
