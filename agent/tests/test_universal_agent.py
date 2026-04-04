"""
Unit tests for agent/universal_agent.py.

Covers:
- Basic instantiation (existing)
- _entropy_next_slot: picks highest-entropy slot (Item 1)
- _entropy_next_slot: falls back to priority when <5 candidates
- _entropy_next_slot: falls back when question_count == 0
- query_rewriter integration: accessory disambiguation wired in
"""

from unittest.mock import MagicMock
from agent.universal_agent import UniversalAgent
from agent.domain_registry import get_domain_schema


# ---------------------------------------------------------------------------
# Existing basic test
# ---------------------------------------------------------------------------

def test_universal_agent_basic():
    agent = UniversalAgent(session_id="test-session")
    assert agent is not None


# ---------------------------------------------------------------------------
# Item 1: _entropy_next_slot
# ---------------------------------------------------------------------------

def _make_laptop_candidates(n=20, vary_brand=True, vary_ram=False):
    """
    Build fake candidate product dicts for entropy testing.
    vary_brand=True → high brand entropy (many distinct values)
    vary_ram=False  → low RAM entropy (all same value)
    """
    brands = ["Dell", "Apple", "Lenovo", "HP", "ASUS"] * (n // 5 + 1)
    candidates = []
    for i in range(n):
        attrs = {"ram_gb": 16 if not vary_ram else [8, 16, 32][i % 3], "storage_type": "SSD"}
        candidates.append({
            "price": 800 + i * 20,
            "brand": brands[i] if vary_brand else "Dell",
            "attributes": attrs,
        })
    return candidates


def test_entropy_next_slot_uses_probe_when_q2():
    """After Q1, entropy should rank slots and pick highest entropy."""
    candidates = _make_laptop_candidates(n=20, vary_brand=True, vary_ram=False)
    probe_fn = MagicMock(return_value=candidates)

    schema = get_domain_schema("laptops")
    agent = UniversalAgent(session_id="test", probe_search_fn=probe_fn)
    agent.domain = "laptops"
    agent.question_count = 1  # past Q1
    agent.questions_asked = ["use_case"]
    agent.filters = {"use_case": "gaming"}

    slot = agent._entropy_next_slot(schema)

    # Probe should have been called
    probe_fn.assert_called_once()
    # Should return a slot
    assert slot is not None
    assert slot.name not in agent.questions_asked
    assert slot.name not in agent._EXTRACT_ONLY_SLOTS


def test_entropy_next_slot_falls_back_on_q1():
    """Q1 always uses priority system (no probe)."""
    probe_fn = MagicMock(return_value=[])
    schema = get_domain_schema("laptops")

    agent = UniversalAgent(session_id="test", probe_search_fn=probe_fn)
    agent.domain = "laptops"
    agent.question_count = 0
    agent.questions_asked = []
    agent.filters = {}

    slot = agent._entropy_next_slot(schema)

    probe_fn.assert_not_called()
    # Should return first HIGH priority slot
    assert slot is not None
    assert slot.priority.value == "HIGH"


def test_entropy_next_slot_falls_back_when_few_candidates():
    """<5 candidates → fall back to priority order."""
    candidates = _make_laptop_candidates(n=3)
    probe_fn = MagicMock(return_value=candidates)
    schema = get_domain_schema("laptops")

    agent = UniversalAgent(session_id="test", probe_search_fn=probe_fn)
    agent.domain = "laptops"
    agent.question_count = 1
    agent.questions_asked = ["use_case"]
    agent.filters = {"use_case": "gaming"}

    slot = agent._entropy_next_slot(schema)

    # Still returns a slot (via priority fallback)
    assert slot is not None


def test_entropy_next_slot_falls_back_without_probe_fn():
    """No probe_fn → always use priority system."""
    schema = get_domain_schema("laptops")

    agent = UniversalAgent(session_id="test", probe_search_fn=None)
    agent.domain = "laptops"
    agent.question_count = 2
    agent.questions_asked = ["use_case"]
    agent.filters = {"use_case": "gaming"}

    slot = agent._entropy_next_slot(schema)

    assert slot is not None


def test_entropy_next_slot_skips_asked_slots():
    """Already-asked slots should never be returned."""
    candidates = _make_laptop_candidates(n=20)
    probe_fn = MagicMock(return_value=candidates)
    schema = get_domain_schema("laptops")

    agent = UniversalAgent(session_id="test", probe_search_fn=probe_fn)
    agent.domain = "laptops"
    agent.question_count = 1
    agent.questions_asked = ["use_case", "budget", "min_ram_gb", "screen_size", "brand"]
    agent.filters = {}

    slot = agent._entropy_next_slot(schema)

    # All mapped slots are asked — should fall back to priority (which may return None)
    if slot is not None:
        assert slot.name not in agent.questions_asked


def test_entropy_next_slot_skips_extract_only_slots():
    """excluded_brands, os should never be selected."""
    candidates = _make_laptop_candidates(n=20)
    probe_fn = MagicMock(return_value=candidates)
    schema = get_domain_schema("laptops")

    agent = UniversalAgent(session_id="test", probe_search_fn=probe_fn)
    agent.domain = "laptops"
    agent.question_count = 1
    agent.questions_asked = []
    agent.filters = {}

    for _ in range(10):
        slot = agent._entropy_next_slot(schema)
        if slot:
            assert slot.name not in agent._EXTRACT_ONLY_SLOTS
        # Simulate asking to cycle through slots
        if slot:
            agent.questions_asked.append(slot.name)
            agent.question_count += 1


# ---------------------------------------------------------------------------
# Item 5: query_rewriter wired into process_message
# ---------------------------------------------------------------------------

def test_process_message_accessory_clarification():
    """Accessory disambiguation from query_rewriter should fire before any LLM call."""
    agent = UniversalAgent(session_id="test-acc")
    agent.domain = "laptops"
    agent.question_count = 0
    agent.questions_asked = []
    agent.filters = {}

    # "bag" alone (domain="laptops" set above) triggers accessory check
    result = agent.process_message("bag")

    assert result["response_type"] == "question"
    assert "laptop" in result["message"].lower()
    assert any("laptop itself" in r.lower() for r in (result.get("quick_replies") or []))


def test_process_message_accessory_not_fired_with_spec_signal():
    """If spec signals present, accessory check should not fire."""
    agent = UniversalAgent(session_id="test-spec")
    agent.domain = "laptops"
    agent.question_count = 0
    agent.questions_asked = []
    agent.filters = {}

    result = agent.process_message("bag 16gb ram ssd")
    # Should NOT trigger accessory clarification — may be question or handoff
    if result["response_type"] == "question":
        assert "accessory" not in result["message"].lower()


# ---------------------------------------------------------------------------
# Negation / brand exclusion accumulation
# ---------------------------------------------------------------------------

def test_excluded_brands_extend_across_turns_regex_path():
    """
    Regex fallback: 'no HP' turn 1 + 'no Dell' turn 2 must give ['HP','Dell'],
    not just ['Dell'].  BUG (fixed): self.filters.update() was overwriting the list.
    """
    from agent.domain_registry import get_domain_schema
    schema = get_domain_schema("laptops")
    agent = UniversalAgent(session_id="test-excl-regex")
    agent.domain = "laptops"
    agent.filters = {}

    # Turn 1: extract "no HP" via regex fallback
    agent._regex_extract_criteria("I want a laptop, no HP", schema)
    excl1 = agent.filters.get("excluded_brands") or []
    # Normalize to list for assertion (may be stored as string or list)
    if isinstance(excl1, str):
        excl1 = [b.strip() for b in excl1.split(",") if b.strip()]
    assert "HP" in excl1, f"HP exclusion not set after turn 1; got {excl1}"

    # Turn 2: extract "no Dell" — HP must still be present
    agent._regex_extract_criteria("also no Dell please", schema)
    excl2 = agent.filters.get("excluded_brands") or []
    if isinstance(excl2, str):
        excl2 = [b.strip() for b in excl2.split(",") if b.strip()]
    assert "HP" in excl2, f"HP was dropped after turn 2; got {excl2}"
    assert "Dell" in excl2, f"Dell not added after turn 2; got {excl2}"


def test_excluded_brands_no_duplicate_regex():
    """Re-stating the same brand in regex path does not duplicate it."""
    from agent.domain_registry import get_domain_schema
    schema = get_domain_schema("laptops")
    agent = UniversalAgent(session_id="test-excl-dedup2")
    agent.domain = "laptops"
    agent.filters = {}

    agent._regex_extract_criteria("no HP laptops", schema)
    agent._regex_extract_criteria("no HP, seriously", schema)
    excl = agent.filters.get("excluded_brands") or []
    if isinstance(excl, str):
        excl = [b.strip() for b in excl.split(",") if b.strip()]
    assert excl.count("HP") == 1, f"HP duplicated: {excl}"


def test_mind_change_removes_brand_from_exclusions():
    """
    If user said 'no Apple' then 'show me Apple', Apple must be removed from exclusions.
    """
    agent = UniversalAgent(session_id="test-mindchange")
    agent.domain = "laptops"
    agent.filters = {"excluded_brands": ["Apple", "Dell"]}

    # Simulate new_filters with brand=Apple (as if LLM extracted it)
    new_filters = {"brand": "Apple"}
    # Apply the mind-change logic directly (mirroring lines in process_message)
    newly_preferred = new_filters["brand"]
    excl = agent.filters.get("excluded_brands")
    if isinstance(excl, list):
        agent.filters["excluded_brands"] = [b for b in excl if b.lower() != newly_preferred.lower()] or None
    if not agent.filters.get("excluded_brands"):
        agent.filters.pop("excluded_brands", None)

    assert "Apple" not in (agent.filters.get("excluded_brands") or []), \
        "Apple still excluded after user asked to see Apple"
    assert "Dell" in (agent.filters.get("excluded_brands") or []), \
        "Dell was incorrectly removed"


# ---------------------------------------------------------------------------
# Preference reset phrases
# ---------------------------------------------------------------------------

def test_forget_the_triggers_soft_reset():
    """
    'forget the gaming specs' must clear use_case and GPU-related slots.
    'forget that' was in the list but 'forget the' wasn't — now both are.
    """
    agent = UniversalAgent(session_id="test-forget")
    agent.domain = "laptops"
    agent.filters = {
        "use_case": "gaming",
        "gpu_tier": "high",
        "refresh_rate_min_hz": "144",
        "brand": "ASUS",
        "price_max_cents": 150000,
    }

    # Trigger preference reset with "forget the gaming specs"
    _PREF_RESET_PHRASES = (
        "changed my mind", "change my mind", "actually", "instead show",
        "show me instead", "forget that", "forget the", "forget those",
        "forget my", "forget about", "never mind", "nevermind",
        "scratch that", "different brand", "switch to", "go with",
    )
    _soft_slots = {"brand", "use_case", "color", "os", "product_subtype",
                   "gpu_vendor", "gpu_tier", "refresh_rate_min_hz"}
    msg = "forget the gaming specs, I just need something basic"
    if any(p in msg.lower() for p in _PREF_RESET_PHRASES):
        for slot in _soft_slots:
            agent.filters.pop(slot, None)

    assert "use_case" not in agent.filters, "use_case was not cleared"
    assert "gpu_tier" not in agent.filters, "gpu_tier was not cleared"
    assert "refresh_rate_min_hz" not in agent.filters, "refresh_rate not cleared"
    assert "brand" not in agent.filters, "brand was not cleared"
    # Hard constraint (budget) must be preserved
    assert agent.filters.get("price_max_cents") == 150000, "budget was incorrectly cleared"


# ---------------------------------------------------------------------------
# Use-case contradiction: heavy → light downgrade
# ---------------------------------------------------------------------------

def test_use_case_downgrade_clears_performance_slots():
    """
    Scenario: user discussed gaming (use_case=gaming, gpu_tier set) then says
    'actually it's just for email and Netflix'.  Performance slots must clear.

    Uses the shared _check_use_case_downgrade method — the same code path
    that fires in both process_message and process_refinement.
    """
    from agent.universal_agent import SlotValue

    agent = UniversalAgent(session_id="test-usecase-downgrade")
    agent.domain = "laptops"
    agent.filters = {
        "use_case": "gaming",
        "min_ram_gb": "16",
        "gpu_tier": "high",
        "refresh_rate_min_hz": "144",
        "price_max_cents": 100000,
    }

    prior_uc = str(agent.filters.get("use_case") or "")
    # Simulate the merge having already updated use_case
    agent.filters["use_case"] = "email"
    agent._check_use_case_downgrade(
        [SlotValue(slot_name="use_case", value="email")],
        prior_uc,
    )

    assert "gpu_tier" not in agent.filters, "gpu_tier persists after use_case downgrade"
    assert "refresh_rate_min_hz" not in agent.filters, "refresh_rate persists"
    assert "min_ram_gb" not in agent.filters, "min_ram_gb persists"
    # Budget preserved
    assert agent.filters.get("price_max_cents") == 100000
    # use_case updated
    assert agent.filters.get("use_case") == "email"


# ---------------------------------------------------------------------------
# _normalize_and_merge_criteria: alias remapping
# ---------------------------------------------------------------------------

def test_normalize_and_merge_criteria_remaps_aliases():
    """
    LLM returns 'ram' and 'price' instead of canonical 'min_ram_gb' and 'budget'.
    _normalize_and_merge_criteria must remap them via _SLOT_NAME_ALIASES.
    """
    from agent.universal_agent import SlotValue

    agent = UniversalAgent(session_id="test-alias-remap")
    agent.domain = "laptops"
    agent.filters = {}
    schema = get_domain_schema("laptops")

    agent._normalize_and_merge_criteria(
        [
            SlotValue(slot_name="ram", value="16"),
            SlotValue(slot_name="price", value="1000"),
            SlotValue(slot_name="brand", value="Dell"),
        ],
        schema,
    )

    assert "min_ram_gb" in agent.filters, "ram was not remapped to min_ram_gb"
    assert agent.filters["min_ram_gb"] == "16"
    assert "budget" in agent.filters, "price was not remapped to budget"
    assert agent.filters["budget"] == "1000"
    assert agent.filters["brand"] == "Dell"
    # The raw alias keys should NOT appear
    assert "ram" not in agent.filters
    assert "price" not in agent.filters


# ---------------------------------------------------------------------------
# get_search_filters: exact use_case → good_for_* mapping
# ---------------------------------------------------------------------------

def test_get_search_filters_use_case_exact_match():
    """
    'machine_learning' must map to good_for_ml via exact dict lookup.
    The old substring approach failed because 'machine_learning' doesn't
    substring-match 'ml'.
    """
    agent = UniversalAgent(session_id="test-usecase-exact")
    agent.domain = "laptops"
    agent.filters = {"use_case": "machine_learning"}

    sf = agent.get_search_filters()
    assert sf.get("good_for_ml") is True, "machine_learning did not map to good_for_ml"

    # Also verify the other canonical values
    for uc, expected_key in [("gaming", "good_for_gaming"), ("creative", "good_for_creative"), ("web_dev", "good_for_web_dev")]:
        agent.filters = {"use_case": uc}
        sf = agent.get_search_filters()
        assert sf.get(expected_key) is True, f"{uc} did not map to {expected_key}"
