"""Quick integration test for use-case downgrade across scenarios B, C, and edge case."""
import json
from agent.universal_agent import UniversalAgent, SlotValue
from agent.domain_registry import get_domain_schema

schema = get_domain_schema("laptops")

def show(agent):
    sf = agent.get_search_filters()
    clean = {k: v for k, v in sf.items() if not k.startswith("_")}
    print("  agent.filters:", json.dumps(agent.filters))
    print("  search_filters:", json.dumps(clean))


print("=== SCENARIO B: Gaming -> email ===")
a = UniversalAgent(session_id="b")
a.domain = "laptops"
a.filters = {"use_case": "gaming", "min_ram_gb": "32", "budget": "under2000"}
print("Turn 1:")
show(a)
prior = str(a.filters.get("use_case") or "")
c = [SlotValue(slot_name="use_case", value="email")]
a._normalize_and_merge_criteria(c, schema)
a._check_use_case_downgrade(c, prior)
print("Turn 2:")
show(a)
assert "min_ram_gb" not in a.filters, "FAIL: min_ram_gb not cleared"
assert "good_for_gaming" not in a.get_search_filters(), "FAIL: good_for_gaming not cleared"
assert a.filters.get("budget") == "under2000", "FAIL: budget was cleared"
print("PASS")

print("")
print("=== SCENARIO C: ML -> browsing ===")
a2 = UniversalAgent(session_id="c")
a2.domain = "laptops"
a2.filters = {"use_case": "machine_learning", "min_ram_gb": "32"}
print("Turn 1:")
show(a2)
prior2 = str(a2.filters.get("use_case") or "")
c2 = [SlotValue(slot_name="use_case", value="browsing")]
a2._normalize_and_merge_criteria(c2, schema)
a2._check_use_case_downgrade(c2, prior2)
print("Turn 2:")
show(a2)
assert "min_ram_gb" not in a2.filters, "FAIL: min_ram_gb not cleared"
sf2 = {k: v for k, v in a2.get_search_filters().items() if not k.startswith("_")}
assert "good_for_ml" not in sf2, "FAIL: good_for_ml not cleared"
print("PASS")

print("")
print("=== EDGE: email -> gaming (no downgrade) ===")
a3 = UniversalAgent(session_id="e")
a3.domain = "laptops"
a3.filters = {"use_case": "email", "budget": "under500"}
print("Turn 1:")
show(a3)
prior3 = str(a3.filters.get("use_case") or "")
c3 = [SlotValue(slot_name="use_case", value="gaming")]
a3._normalize_and_merge_criteria(c3, schema)
a3._check_use_case_downgrade(c3, prior3)
print("Turn 2:")
show(a3)
assert a3.filters.get("budget") == "under500", "FAIL: budget was cleared"
assert a3.filters.get("use_case") == "gaming", "FAIL: use_case not updated"
print("PASS")

print("\nAll scenarios passed.")
