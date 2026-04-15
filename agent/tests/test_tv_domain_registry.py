from agent.domain_registry import get_domain_schema, list_domains, SlotPriority

def test_tv_schema_in_registry():
    assert "tvs" in list_domains()
    schema = get_domain_schema("tvs")
    assert schema is not None
    assert schema.domain == "tvs"

def test_tv_schema_has_expected_slots():
    schema = get_domain_schema("tvs")
    slot_names = {s.name for s in schema.slots}
    assert slot_names == {"budget", "use_case", "screen_size", "panel_type", "brand", "smart_platform", "excluded_brands"}

def test_tv_slot_priorities():
    schema = get_domain_schema("tvs")
    by_priority = schema.get_slots_by_priority()
    high_names = {s.name for s in by_priority[SlotPriority.HIGH]}
    medium_names = {s.name for s in by_priority[SlotPriority.MEDIUM]}
    low_names = {s.name for s in by_priority[SlotPriority.LOW]}
    assert high_names == {"budget", "use_case", "screen_size"}
    assert medium_names == {"panel_type", "brand"}
    assert low_names == {"smart_platform", "excluded_brands"}

def test_tv_panel_type_allowed_values():
    schema = get_domain_schema("tvs")
    panel_slot = next(s for s in schema.slots if s.name == "panel_type")
    assert panel_slot.allowed_values == ["OLED", "QLED", "LED", "Mini LED"]

def test_tv_brand_allowed_values():
    schema = get_domain_schema("tvs")
    brand_slot = next(s for s in schema.slots if s.name == "brand")
    assert "Samsung" in brand_slot.allowed_values
    assert "LG" in brand_slot.allowed_values
    assert "Sony" in brand_slot.allowed_values
    assert "TCL" in brand_slot.allowed_values
    assert len(brand_slot.allowed_values) == 10
