from agent.prompts import DOMAIN_DETECTION_PROMPT

def test_prompts_basic():
    assert isinstance(DOMAIN_DETECTION_PROMPT, str)

# Add latency, accuracy, and error handling tests here
