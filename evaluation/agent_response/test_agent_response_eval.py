"""
Pytest for Pipeline 1: Agent response evaluation.

Run with:
  pytest evaluation/agent_response/ -v
  pytest evaluation/agent_response/test_agent_response_eval.py -v
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure repo root and mcp-server are on path (agent imports app from mcp-server)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SERVER = _REPO_ROOT / "mcp-server"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if _MCP_SERVER.exists() and str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass


@pytest.fixture(scope="module")
def eval_results():
    """Run the evaluation pipeline once per test module and return results."""
    from evaluation.agent_response.run_eval import (
        load_test_cases,
        run_one,
        evaluate_with_deepeval,
    )
    import asyncio
    from agent.chat_endpoint import process_chat, ChatRequest

    test_cases = load_test_cases()
    async def run_all():
        return [await run_one(tc) for tc in test_cases]
    rows = asyncio.run(run_all())
    results = evaluate_with_deepeval(rows, test_cases)
    return results


def test_eval_results_structure(eval_results):
    """Results have required columns: test_id, user_query, agent_message, score, passed, details."""
    for r in eval_results:
        assert "test_id" in r
        assert "user_query" in r
        assert "agent_message" in r
        assert "score" in r
        assert "passed" in r
        assert "details" in r
        assert isinstance(r["score"], (int, float))
        assert isinstance(r["passed"], bool)


def test_eval_results_file_written():
    """Running run_eval writes results to results/agent_response_eval_results.json."""
    from evaluation.agent_response.run_eval import main
    main()
    results_dir = Path(__file__).resolve().parent / "results"
    json_path = results_dir / "agent_response_eval_results.json"
    assert json_path.exists(), f"Expected {json_path} to exist after run_eval"
    with open(json_path) as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert all("test_id" in row and "score" in row for row in data)


def test_agent_responds_to_greeting(eval_results):
    """At least one test is a greeting; agent should return a non-empty message for it."""
    greeting = next((r for r in eval_results if r["test_id"] == "greeting_1"), None)
    if greeting is None:
        pytest.skip("greeting_1 test case not in dataset")
    assert greeting["agent_message"], "Agent should respond to greeting with non-empty message"
