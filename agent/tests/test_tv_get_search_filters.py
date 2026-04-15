import os
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from agent.universal_agent import UniversalAgent

def _make_tv_agent(filters: dict) -> UniversalAgent:
    agent = UniversalAgent.__new__(UniversalAgent)
    agent.domain = "tvs"
    agent.filters = filters
    agent.questions_asked = []
    agent.question_count = 0
    agent.history = []
    agent.client = None
    return agent

def test_tv_budget_to_price_max_cents():
    agent = _make_tv_agent({"budget": "under1000"})
    sf = agent.get_search_filters()
    assert sf["price_max_cents"] == 100000

def test_tv_budget_range():
    agent = _make_tv_agent({"budget": "500-1500"})
    sf = agent.get_search_filters()
    assert sf["price_max_cents"] == 150000

def test_tv_brand_filter():
    agent = _make_tv_agent({"brand": "Samsung"})
    sf = agent.get_search_filters()
    assert sf["brand"] == "Samsung"

def test_tv_panel_type_filter():
    agent = _make_tv_agent({"panel_type": "OLED"})
    sf = agent.get_search_filters()
    assert sf["panel_type"] == "OLED"

def test_tv_smart_platform_filter():
    agent = _make_tv_agent({"smart_platform": "Roku"})
    sf = agent.get_search_filters()
    assert sf["smart_platform"] == "Roku"

def test_tv_screen_size_exact():
    agent = _make_tv_agent({"screen_size": "65"})
    sf = agent.get_search_filters()
    assert "min_screen_size" in sf or "max_screen_size" in sf

def test_tv_use_case_no_good_for_flags():
    agent = _make_tv_agent({"use_case": "gaming"})
    sf = agent.get_search_filters()
    assert "good_for_gaming" not in sf
    assert sf.get("_soft_preferences", {}).get("use_case") == "gaming"
