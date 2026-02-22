import pytest
from agent.chat_endpoint import process_chat, ChatRequest

def test_chat_endpoint_basic():
    req = ChatRequest(message="Hello")
    assert req.message == "Hello"

# Add latency, accuracy, and error handling tests here
