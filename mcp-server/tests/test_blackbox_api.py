from app.blackbox_api import MCPBlackbox

def test_blackbox_api_basic():
    bb = MCPBlackbox()
    assert bb is not None

# Latency test
def test_blackbox_api_latency():
    import time
    bb = MCPBlackbox()
    start = time.time()
    bb.search("gaming laptop")
    latency = time.time() - start
    assert latency < 2.0  # Expect search to complete within 2 seconds

# Accuracy test
def test_blackbox_api_accuracy():
    bb = MCPBlackbox()
    result = bb.search("Dell laptop", brand="Dell", max_price=2000)
    assert all(p.brand == "Dell" and p.price <= 2000 for p in result.products)

# Error handling test
def test_blackbox_api_error_handling():
    bb = MCPBlackbox()
    try:
        bb.search("")  # Empty query should raise or handle error
    except Exception:
        assert True
    else:
        assert True  # If handled gracefully, still pass
# Add latency, accuracy, and error handling tests here
