"""
Pipeline 1: Evaluate agent responses using a custom LLM-as-judge (no third-party eval lib).

Runnable as:
  python -m evaluation.agent_response.run_eval

Judge: OpenAI (OPENAI_JUDGE_MODEL or gpt-4o-mini) or Ollama when OLLAMA_MODEL_NAME is set.
Requires OPENAI_API_KEY for the chat agent (and for the judge when not using Ollama).
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


def get_messages_from_test_case(test_case: dict) -> list:
    """Return the list of user messages for this test case (multi-turn or single).
    - If test_case has "messages" (non-empty list), use it.
    - Else use "user_query" as a single message (backward compatible).
    """
    messages = test_case.get("messages")
    if isinstance(messages, list) and len(messages) > 0:
        return [str(m).strip() for m in messages if str(m).strip()]
    user_query = test_case.get("user_query", "").strip()
    if user_query:
        return [user_query]
    return []


def generate_follow_up_user_message(
    initial_query: str,
    user_messages: list,
    agent_messages: list,
) -> str:
    """Generate one short, plausible user reply given the conversation so far.
    Used for dynamic multi-turn: the next user message is based on the agent's
    last response (e.g. answer a clarifying question, or ask to see recommendations).
    Not hardcoded—uses a small LLM call.
    """
    if not agent_messages:
        return ""
    from openai import OpenAI
    client = OpenAI()
    last_agent = (agent_messages[-1] or "").strip()[:1500]
    convo = ""
    for t, (u, a) in enumerate(zip(user_messages, agent_messages)):
        convo += f"User: {u}\nAssistant: {(a or '')[:400]}\n"
    prompt = (
        "You are simulating a user in a laptop recommendation chat. The user's initial request was:\n"
        f'"{initial_query[:300]}"\n\n'
        "Conversation so far:\n" + convo + "\n"
        "The assistant just said (last message):\n" + last_agent + "\n\n"
        "Write exactly one short, natural user reply (1-2 sentences). "
        "If the assistant asked a clarifying question (e.g. RAM, budget, screen size), have the user answer it. "
        "If the assistant offered recommendations, have the user say they want to see them or thanks. "
        "Stay on-topic; do not introduce new requests. Reply with only the user's message, no quotes or labels."
    )
    try:
        completion = client.chat.completions.create(
            model=_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You output only the next user message, nothing else."},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=120,
        )
        raw = (completion.choices[0].message.content or "").strip()
        return raw[:500] if raw else ""
    except Exception:
        return ""


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


# Custom LLM-as-judge (replaces DeepEval)
try:
    from .custom_judge import evaluate_one as _judge_one, DEFAULT_CRITERIA as _JUDGE_CRITERIA, THRESHOLD as _JUDGE_THRESHOLD
except ImportError:
    from evaluation.agent_response.custom_judge import evaluate_one as _judge_one, DEFAULT_CRITERIA as _JUDGE_CRITERIA, THRESHOLD as _JUDGE_THRESHOLD

# Baseline: recommendations restricted to Supabase (same product store as agent). No agent logic.
# We extract minimal filters from the query, search the DB, and format a response from results only.
_OPENAI_MODEL = os.environ.get("OPENAI_MODEL_BASELINE") or "gpt-4o-mini"


def _extract_baseline_filters(user_query: str) -> dict:
    """Extract price_max_cents and optional brand from the user query via a small LLM call."""
    from openai import OpenAI
    client = OpenAI()
    completion = client.chat.completions.create(
        model=_OPENAI_MODEL,
        messages=[
            {"role": "system", "content": (
                "Extract from the user's laptop request: (1) maximum budget in dollars as a number, "
                "or null if not stated; (2) brand name if mentioned (e.g. HP, Dell, Apple), or null. "
                "Reply with only a JSON object: {\"price_max_dollars\": number or null, \"brand\": string or null}."
            )},
            {"role": "user", "content": user_query},
            ],
        max_completion_tokens=80,
    )
    raw = (completion.choices[0].message.content or "").strip()
    try:
        # Handle optional markdown code block
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        out = json.loads(raw)
        price_max_dollars = out.get("price_max_dollars")
        brand = out.get("brand")
        filters = {"category": "electronics", "product_type": "laptop"}
        if isinstance(price_max_dollars, (int, float)) and price_max_dollars > 0:
            filters["price_max_cents"] = int(round(price_max_dollars * 100))
        if isinstance(brand, str) and brand.strip():
            filters["brand"] = brand.strip()
        return filters
    except (json.JSONDecodeError, TypeError):
        return {"category": "electronics", "product_type": "laptop"}


def _baseline_search_supabase(filters: dict, limit: int = 9) -> list:
    """Return product dicts from the same Supabase/product store the agent uses."""
    try:
        from app.tools.supabase_product_store import get_product_store
        store = get_product_store()
        return store.search_products(filters, limit=limit) or []
    except Exception:
        return []


def _products_to_catalog_text(products: list, max_items: int = 6) -> str:
    """Turn product dicts into a short catalog list for the LLM context."""
    parts = []
    for p in (products or [])[:max_items]:
        name = (p.get("name") or p.get("title") or "Laptop").strip()
        price = p.get("price") or p.get("price_value")
        if price is not None:
            try:
                price = f"${float(price):.0f}"
            except (TypeError, ValueError):
                price = ""
        else:
            price = ""
        if price:
            parts.append(f"- {name} ({price})")
        else:
            parts.append(f"- {name}")
    return "\n".join(parts) if parts else "(none)"


def _baseline_llm_response(user_query: str, catalog_text: str, has_products: bool) -> str:
    """Let the model give its normal response; it may only recommend products from catalog_text."""
    from openai import OpenAI
    client = OpenAI()
    if has_products:
        system = (
            "You are a helpful laptop recommendation assistant. The user asked a question below. "
            "You have access ONLY to the following products from our catalog (name and price). "
            "Do not invent or mention any other products."
        )
        user_content = f"Products in our catalog:\n{catalog_text}\n\nUser request: {user_query}"
    else:
        system = (
            "You are a helpful laptop recommendation assistant. The user asked a question below. "
            "We have no products in our catalog that match their criteria. "
        )
        user_content = f"User request: {user_query}"
    completion = client.chat.completions.create(
        model=_OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=500,
    )
    raw = getattr(completion.choices[0].message, "content", None) or ""
    return (raw if isinstance(raw, str) else "").strip()


def run_baseline(test_case: dict) -> dict:
    """Return a result row: model gives normal response, but recs come only from Supabase catalog."""
    user_query = test_case["user_query"].strip()
    filters = _extract_baseline_filters(user_query)
    products = _baseline_search_supabase(filters)
    catalog_text = _products_to_catalog_text(products)
    message = _baseline_llm_response(user_query, catalog_text, has_products=bool(products))
    rec_summary = f"{len(products)} product(s)" if products else None
    return {
        "test_id": test_case["test_id"],
        "user_query": test_case["user_query"],
        "agent_message": message,
        "recommendations_summary": rec_summary,
        "response_type": "recommendations" if products else "question",
    }


async def run_one(test_case: dict) -> dict:
    """Run a full conversation (one or multiple turns) for one test case and return result row (no score yet).
    Uses a single session_id per test case so the agent keeps state across turns.
    When test case has only one message and MULTI_TURN_DYNAMIC_TURNS is set (e.g. 2), additional user
    messages are generated dynamically from the agent's responses (no hardcoded follow-ups).
    """
    messages = get_messages_from_test_case(test_case)
    first_user_query = test_case.get("user_query", "").strip() or (messages[0] if messages else "")

    if not messages:
        return {
            "test_id": test_case["test_id"],
            "user_query": first_user_query,
            "conversation_input": "",
            "agent_message": "",
            "error": "no user messages in test case",
            "recommendations_summary": None,
            "response_type": None,
        }

    # Dynamic multi-turn: if only one message and env set, add that many extra turns by generating
    # the next user message from the agent's last response (LLM-based, not hardcoded).
    num_dynamic = 0
    try:
        num_dynamic = max(0, int(os.environ.get("MULTI_TURN_DYNAMIC_TURNS", "0") or "0"))
    except (TypeError, ValueError):
        pass
    if len(messages) == 1 and num_dynamic > 0:
        total_turns = 1 + num_dynamic
    else:
        total_turns = len(messages)

    session_id = None
    agent_messages = []
    last_response = None
    try:
        for turn_idx in range(total_turns):
            if turn_idx < len(messages):
                user_msg = messages[turn_idx]
            else:
                user_msg = generate_follow_up_user_message(first_user_query, messages, agent_messages)
                if not user_msg:
                    break
                messages.append(user_msg)
            req = ChatRequest(message=user_msg.strip(), session_id=session_id)
            response = await process_chat(req)
            session_id = response.session_id
            agent_messages.append(response.message or "")
            last_response = response
        recs = last_response.recommendations if last_response else None
        rec_summary = None
        if recs:
            flat = [item for row in recs for item in row]
            rec_summary = f"{len(recs)} row(s), {len(flat)} item(s)"
        # Format for judge: full conversation (user side) and full agent side
        conversation_input = "\n\n".join(f"User (turn {t+1}): {m}" for t, m in enumerate(messages))
        if len(agent_messages) == 1:
            agent_message = agent_messages[0] or ""
        else:
            parts = [f"Assistant (turn {t+1}): {m}" for t, m in enumerate(agent_messages) if m]
            agent_message = "\n\n---\n\n".join(parts) if parts else "\n\n---\n\n".join(agent_messages)
        return {
            "test_id": test_case["test_id"],
            "user_query": first_user_query,
            "conversation_input": conversation_input,
            "agent_message": agent_message,
            "recommendations_summary": rec_summary,
            "response_type": last_response.response_type if last_response else None,
        }
    except Exception as e:
        conversation_input = "\n\n".join(f"User (turn {t+1}): {m}" for t, m in enumerate(messages))
        return {
            "test_id": test_case["test_id"],
            "user_query": first_user_query,
            "conversation_input": conversation_input,
            "agent_message": "\n\n---\n\n".join(agent_messages) if agent_messages else "",
            "error": str(e),
            "recommendations_summary": None,
            "response_type": None,
        }


def evaluate_with_custom_judge(rows: list, test_cases: list) -> list:
    """Run custom LLM-as-judge on each row (multi-turn: full conversation as input).
    Mimics G-Eval: input = user/conversation, actual_output = agent reply, expected = criteria.
    """
    id_to_case = {tc["test_id"]: tc for tc in test_cases}
    results = []
    for row in rows:
        test_id = row["test_id"]
        tc = id_to_case.get(test_id, {})
        expected = tc.get("expected_topic_or_criteria") or _JUDGE_CRITERIA
        judge_input = row.get("conversation_input") or row.get("user_query") or ""
        actual_output = row.get("agent_message") or ""
        if not (actual_output or "").strip():
            score = 0.0
            passed = False
            details = {"error": "empty agent response; cannot evaluate relevance", "threshold": _JUDGE_THRESHOLD}
        else:
            try:
                out = _judge_one(
                    judge_input=judge_input,
                    actual_output=actual_output,
                    expected_output=expected,
                    criteria=_JUDGE_CRITERIA,
                    threshold=_JUDGE_THRESHOLD,
                )
                score = out["score"]
                passed = out["passed"]
                details = {"reason": out["reason"], "threshold": out["threshold"]}
            except Exception as e:
                score = 0.0
                passed = False
                details = {"error": str(e), "threshold": _JUDGE_THRESHOLD}
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
        print("Baseline mode: Supabase-only recommendations (extract filters -> search DB -> format from results).")
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

    # Optional: skip agent and load previously saved rows (re-run only the judge)
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
        print(f"Loaded {len(rows)} rows and {len(test_cases)} test cases. Running judge only.")
    elif not baseline_only:
        rows = asyncio.run(run_all())
        out_dir = _THIS_DIR / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        rows_file = out_dir / "agent_response_rows.json"
        with open(rows_file, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "test_cases": test_cases}, f, indent=2)
        print(f"Saved {len(rows)} agent responses to {rows_file}")

    print("Evaluating with custom LLM-as-judge...")
    results = evaluate_with_custom_judge(rows, test_cases)

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
