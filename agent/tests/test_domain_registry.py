from agent.domain_registry import get_domain_schema, list_domains

def test_domain_registry_basic():
    domains = list_domains()
    assert "vehicles" in domains
    schema = get_domain_schema("vehicles")
    assert schema is not None

# Add latency, accuracy, and error handling tests here
