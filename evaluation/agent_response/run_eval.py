"""
Pipeline 1: Evaluate agent responses using DeepEval's LLM-as-judge (G-Eval).

Runnable as:
  python -m evaluation.agent_response.run_eval

Uses an open-source judge via Ollama when OLLAMA_MODEL_NAME is set (e.g. llama3.2,
mistral, deepseek-r1:1.5b). Otherwise uses DeepEval's default model (GPT).
Requires OPENAI_API_KEY (and optionally .env) for the chat agent.
"""

import asyncio
import csv as csv_module
import json
import os
import sys
from pathlib import Path

# Repo root and mcp-server for app/ imports (agent uses app.formatters, app.cache, etc.)
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
_MCP_SERVER = _REPO_ROOT / "mcp-server"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if _MCP_SERVER.exists() and str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))

# Load .env from repo root so OPENAI_API_KEY etc. are set for the agent
try:
    from dotenv import load_dotenv
    _env_path = _REPO_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
    else:
        load_dotenv()
except ImportError:
    pass

from agent.chat_endpoint import process_chat, ChatRequest


def load_test_cases() -> list:
    """Load test cases from the default JSON file (mixed intents)."""
    path = _THIS_DIR / "test_cases.json"
    with open(path, "r") as f:
        return json.load(f)


def load_laptop_test_cases_from_csv() -> list:
    """Load laptop-only test cases from testing/query_data_enriched.csv.
    Uses shortened_query as user_query. All rows in this CSV are laptop queries (SuggestALaptop).
    """
    path = _REPO_ROOT / "testing" / "query_data_enriched.csv"
    if not path.exists():
        raise FileNotFoundError(f"Laptop query CSV not found: {path}")
    test_cases = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv_module.DictReader(f)
        for i, row in enumerate(reader):
            user_query = (row.get("shortened_query") or row.get("original_query") or "").strip()
            if not user_query:
                continue
            test_id = f"laptop_{row.get('id', i)}"
            test_cases.append({
                "test_id": test_id,
                "user_query": user_query,
                "expected_topic_or_criteria": (
                    "The response is helpful if it either (1) provides laptop recommendations that match "
                    "the user's stated needs (budget, use case, preferences), or (2) asks a relevant "
                    "clarifying question (e.g., RAM, brand, screen size) to better tailor recommendations. "
                    "Both recommending and asking one focused follow-up question are valid and on-topic "
                    "for a recommendation assistant."
                ),
            })
    return test_cases


def get_judge_model():
    """Use Ollama (open-source) if configured, else DeepEval default."""
    ollama_model = os.environ.get("OLLAMA_MODEL_NAME", "").strip() or os.environ.get("DEEPEVAL_OLLAMA_MODEL", "").strip()
    if ollama_model:
        try:
            from deepeval.models import OllamaModel
            base_url = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434")
            return OllamaModel(
                model=ollama_model,
                base_url=base_url,
                temperature=0.0,
            )
        except Exception as e:
            print(f"Warning: Ollama model '{ollama_model}' not available ({e}). Using default judge.", file=sys.stderr)
    return None


def build_geval_metric(model=None):
    """Build a G-Eval metric for relevance/helpfulness of the agent response."""
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    kwargs = {
        "name": "Relevance and Helpfulness",
        "criteria": (
            "Determine whether the actual output (the agent's reply) is relevant and helpful "
            "given the user's input. Helpful includes both giving product recommendations and "
            "asking a relevant clarifying question (e.g., RAM, brand, screen size) to narrow "
            "down options. The reply should be on-topic (laptops), coherent, and appropriate "
            "for a recommendation assistant. If expected_topic_or_criteria is provided, use it "
            "to decide whether the response meets the bar."
        ),
        "evaluation_params": [
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        "threshold": 0.5,
    }
    if model is not None:
        kwargs["model"] = model
    return GEval(**kwargs)


# Baseline: one fixed clarifying question for all queries (no agent call).
BASELINE_MESSAGE = (
    "What's your budget for a laptop, and how will you mainly use it? "
    "(e.g. work, gaming, school, creative work)"
)


def run_baseline(test_case: dict) -> dict:
    """Return a result row using the fixed baseline message (no agent)."""
    return {
        "test_id": test_case["test_id"],
        "user_query": test_case["user_query"],
        "agent_message": BASELINE_MESSAGE,
        "recommendations_summary": None,
        "response_type": "question",
    }


async def run_one(test_case: dict) -> dict:
    """Call process_chat for one test case and return result row (no score yet)."""
    req = ChatRequest(message=test_case["user_query"].strip())
    try:
        response = await process_chat(req)
        agent_message = response.message or ""
        recs = response.recommendations
        rec_summary = None
        if recs:
            flat = [item for row in recs for item in row]
            rec_summary = f"{len(recs)} row(s), {len(flat)} item(s)"
        return {
            "test_id": test_case["test_id"],
            "user_query": test_case["user_query"],
            "agent_message": agent_message,
            "recommendations_summary": rec_summary,
            "response_type": response.response_type,
        }
    except Exception as e:
        return {
            "test_id": test_case["test_id"],
            "user_query": test_case["user_query"],
            "agent_message": "",
            "error": str(e),
            "recommendations_summary": None,
            "response_type": None,
        }


def evaluate_with_deepeval(rows: list, test_cases: list) -> list:
    """Run DeepEval G-Eval on each row and add score, passed, details."""
    from deepeval.test_case import LLMTestCase

    id_to_case = {tc["test_id"]: tc for tc in test_cases}
    metric = build_geval_metric(model=get_judge_model())
    results = []
    for row in rows:
        test_id = row["test_id"]
        tc = id_to_case.get(test_id, {})
        expected = tc.get("expected_topic_or_criteria") or ""
        test_case = LLMTestCase(
            input=row["user_query"],
            actual_output=row.get("agent_message") or "",
            expected_output=expected,
        )
        actual = (row.get("agent_message") or "").strip()
        if not actual:
            score = 0.0
            passed = False
            details = {"error": "empty agent response; cannot evaluate relevance"}
        else:
            try:
                metric.measure(test_case)
                score = getattr(metric, "score", None)
                reason = getattr(metric, "reason", None)
                if score is None:
                    score = 0.0
                passed = bool(score >= metric.threshold)
                details = {"reason": reason, "threshold": metric.threshold}
            except Exception as e:
                score = 0.0
                passed = False
                details = {"error": str(e)}
        results.append({
            "test_id": test_id,
            "user_query": row["user_query"],
            "agent_message": row.get("agent_message", ""),
            "score": score,
            "passed": passed,
            "details": details,
            "recommendations_summary": row.get("recommendations_summary"),
            "response_type": row.get("response_type"),
        })
    return results


def main(use_laptop_queries: bool = True, baseline_only: bool = False):
    """Run evaluation. By default uses laptop-only queries from testing/query_data_enriched.csv.
    Set env USE_LAPTOP_QUERIES=0 to use test_cases.json instead.
    If baseline_only=True, use fixed baseline message instead of the agent (no API/Redis).
    """
    use_laptop = os.environ.get("USE_LAPTOP_QUERIES", "1").strip().lower() not in ("0", "false", "no")
    print("Loading test cases...")
    if use_laptop:
        test_cases = load_laptop_test_cases_from_csv()
        max_cases = os.environ.get("MAX_TEST_CASES", "").strip()
        if max_cases and max_cases.isdigit():
            n = int(max_cases)
            test_cases = test_cases[:n]
            print(f"Using first {len(test_cases)} laptop queries (MAX_TEST_CASES={n})")
        else:
            print(f"Using {len(test_cases)} laptop queries from testing/query_data_enriched.csv")
    else:
        test_cases = load_test_cases()
        print(f"Using {len(test_cases)} test cases from test_cases.json")
    if baseline_only:
        print("Baseline mode: using fixed response (no agent).")
        rows = [run_baseline(tc) for tc in test_cases]
    else:
        print(f"Running agent for {len(test_cases)} test cases...")
        if not os.environ.get("OPENAI_API_KEY"):
            print("Warning: OPENAI_API_KEY not set. Agent may fail.", file=sys.stderr)

    async def run_all():
        rows = []
        for tc in test_cases:
            row = await run_one(tc)
            rows.append(row)
        return rows

    # Optional: skip agent and load previously saved rows (for re-running only the judge after installing deepeval)
    load_path = os.environ.get("LOAD_ROWS_FROM", "").strip()
    if load_path and not baseline_only:
        load_file = Path(load_path)
        if not load_file.is_absolute():
            load_file = _THIS_DIR / load_path
        print(f"Loading saved rows from {load_file}...")
        with open(load_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data["rows"]
        test_cases = data["test_cases"]
        print(f"Loaded {len(rows)} rows and {len(test_cases)} test cases. Running DeepEval only.")
    elif not baseline_only:
        rows = asyncio.run(run_all())
        out_dir = _THIS_DIR / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        rows_file = out_dir / "agent_response_rows.json"
        with open(rows_file, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "test_cases": test_cases}, f, indent=2)
        print(f"Saved {len(rows)} agent responses to {rows_file}")

    print("Evaluating with DeepEval (LLM-as-judge)...")
    try:
        results = evaluate_with_deepeval(rows, test_cases)
    except ModuleNotFoundError as e:
        if "deepeval" in str(e).lower():
            print(
                "\nDeepEval is not installed. Install it with:\n  pip install deepeval\n\n"
                "Then re-run scoring only (no need to re-run the agent):\n"
                f"  LOAD_ROWS_FROM=results/agent_response_rows.json python -m evaluation.agent_response.run_eval\n",
                file=sys.stderr,
            )
        raise

    out_dir = _THIS_DIR / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_baseline" if baseline_only else ""
    out_json = out_dir / f"agent_response_eval_results{suffix}.json"
    out_csv = out_dir / f"agent_response_eval_results{suffix}.csv"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {len(results)} results to {out_json}")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv_module.writer(f)
        w.writerow(["test_id", "user_query", "agent_message", "score", "passed", "details"])
        for r in results:
            w.writerow([
                r["test_id"],
                r["user_query"],
                r["agent_message"],
                r["score"],
                r["passed"],
                json.dumps(r["details"]) if isinstance(r["details"], dict) else str(r["details"]),
            ])
    print(f"Wrote CSV to {out_csv}")

    passed = sum(1 for r in results if r["passed"])
    print(f"Passed: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Run agent response evaluation (G-Eval)")
    p.add_argument("--baseline", action="store_true", help="Run baseline only (fixed response, no agent)")
    args = p.parse_args()
    sys.exit(main(baseline_only=args.baseline))
