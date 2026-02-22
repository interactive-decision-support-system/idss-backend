from app.conversation_controller import detect_domain, is_domain_switch, is_short_domain_intent, is_greeting_or_ambiguous

def test_detect_domain():
    domain, reason = detect_domain("I want a gaming laptop")
    assert domain.value == "laptops"
    assert reason in ["keyword_laptop", "domain_intent", "fuzzy_match"]

def test_is_domain_switch():
    assert is_domain_switch("laptops", "vehicles") is True
    assert is_domain_switch("laptops", "laptops") is False

def test_is_short_domain_intent():
    assert is_short_domain_intent("show me books") is True
    assert is_short_domain_intent("random text") is False

def test_is_greeting_or_ambiguous():
    assert is_greeting_or_ambiguous("hi") is True
    assert is_greeting_or_ambiguous("I want a laptop") is False

# Add latency, accuracy, and error handling tests here
