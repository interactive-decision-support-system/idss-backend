from agent.universal_agent import UniversalAgent

def test_fast_domain_map_tv_keywords():
    tv_words = ["tv", "tvs", "television", "televisions", "oled", "qled", "roku", "hisense", "vizio", "tcl"]
    for word in tv_words:
        assert UniversalAgent._FAST_DOMAIN_MAP.get(word) == "tvs", f"'{word}' should map to 'tvs'"

def test_samsung_still_maps_to_laptops():
    # Samsung is ambiguous — should NOT be stolen by TVs
    assert UniversalAgent._FAST_DOMAIN_MAP.get("samsung") != "tvs"

def test_tv_keywords_do_not_overlap_with_laptops():
    tv_exclusive = ["tv", "tvs", "television", "oled", "qled", "roku", "hisense", "vizio", "tcl"]
    for word in tv_exclusive:
        assert UniversalAgent._FAST_DOMAIN_MAP.get(word) == "tvs"
