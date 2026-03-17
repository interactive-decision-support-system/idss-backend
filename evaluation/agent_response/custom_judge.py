"""
Custom LLM-as-judge for agent response evaluation (replaces DeepEval).

Mimics G-Eval style: one LLM call per case with input (user/conversation),
actual_output (agent reply), expected_output (criteria). Returns a score in [0, 1]
and a reason. Pass threshold is 0.5.

Supports OpenAI (default) and Ollama (set OLLAMA_MODEL_NAME) for the judge.
"""

import json
import os
import re


# Default criteria (same conceptual rubric as prior G-Eval)
DEFAULT_CRITERIA = (
    "Determine whether the actual output (the agent's reply) is relevant and helpful "
    "given the user's input. Helpful includes both giving product recommendations and "
    "asking a relevant clarifying question (e.g., RAM, brand, screen size) to narrow "
    "down options. The reply should be on-topic (laptops), coherent, and appropriate "
    "for a recommendation assistant. If expected_topic_or_criteria is provided, use it "
    "to decide whether the response meets the bar."
)

THRESHOLD = 0.5


def _parse_score_and_reason(response_text: str) -> tuple[float, str]:
    """Extract score in [0,1] and reason from judge LLM response. Returns (score, reason)."""
    text = (response_text or "").strip()
    reason = text
    score = 0.0
    # Try JSON first: {"score": 0.7, "reason": "..."}
    try:
        # Find JSON block if present
        m = re.search(r"\{[^{}]*\"score\"[^{}]*\"reason\"[^{}]*\}", text, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            s = obj.get("score")
            if isinstance(s, (int, float)):
                score = max(0.0, min(1.0, float(s)))
            reason = obj.get("reason", text) or text
            return score, reason
        m = re.search(r"\{[^{}]*\}", text)
        if m:
            obj = json.loads(m.group(0))
            s = obj.get("score")
            if isinstance(s, (int, float)):
                score = max(0.0, min(1.0, float(s)))
            reason = obj.get("reason", text) or text
            return score, reason
    except (json.JSONDecodeError, TypeError):
        pass
    # Try "Score: 0.7" or "score: 0.7"
    m = re.search(r"[Ss]core\s*:\s*(\d+\.?\d*)", text)
    if m:
        try:
            score = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    # Reason: rest of text or first line
    if "reason" in text.lower() or "because" in text.lower():
        reason = text
    return score, reason


def judge_with_openai(
    judge_input: str,
    actual_output: str,
    expected_output: str,
    criteria: str = DEFAULT_CRITERIA,
    model: str | None = None,
) -> tuple[float, str]:
    """Call OpenAI API for judge. Returns (score, reason)."""
    from openai import OpenAI
    model = model or os.environ.get("OPENAI_JUDGE_MODEL") or "gpt-4o-mini"
    client = OpenAI()
    user_content = (
        f"**User input (or conversation):**\n{judge_input}\n\n"
        f"**Actual output (agent's reply):**\n{actual_output}\n\n"
        f"**Expected behavior / criteria:**\n{expected_output or criteria}\n\n"
        "**Task:** Score how relevant and helpful the actual output is, from 0.0 (not helpful/off-topic) to 1.0 (fully relevant and helpful). "
        "Reply with a JSON object: {\"score\": <number in [0,1]>, \"reason\": \"<brief explanation>\"}."
    )
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are an evaluator for a laptop recommendation assistant. Output only valid JSON with 'score' and 'reason'."},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=400,
        temperature=0.0,
    )
    raw = (completion.choices[0].message.content or "").strip()
    return _parse_score_and_reason(raw)


def judge_with_ollama(
    judge_input: str,
    actual_output: str,
    expected_output: str,
    criteria: str = DEFAULT_CRITERIA,
    model: str | None = None,
    base_url: str = "http://localhost:11434",
) -> tuple[float, str]:
    """Call Ollama API for judge. Returns (score, reason)."""
    try:
        import urllib.request
        model = model or os.environ.get("OLLAMA_MODEL_NAME") or os.environ.get("DEEPEVAL_OLLAMA_MODEL") or "llama3.2"
        base_url = os.environ.get("LOCAL_MODEL_BASE_URL", base_url).rstrip("/")
        user_content = (
            f"**User input (or conversation):**\n{judge_input}\n\n"
            f"**Actual output (agent's reply):**\n{actual_output}\n\n"
            f"**Expected behavior / criteria:**\n{expected_output or criteria}\n\n"
            "Score how relevant and helpful the actual output is, from 0.0 to 1.0. "
            "Reply with JSON: {\"score\": <number>, \"reason\": \"<brief explanation>\"}."
        )
        body = json.dumps({
            "model": model,
            "prompt": user_content,
            "stream": False,
            "options": {"temperature": 0.0},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        raw = (data.get("response") or "").strip()
        return _parse_score_and_reason(raw)
    except Exception as e:
        return 0.0, str(e)


def evaluate_one(
    judge_input: str,
    actual_output: str,
    expected_output: str,
    criteria: str = DEFAULT_CRITERIA,
    threshold: float = THRESHOLD,
) -> dict:
    """
    Run the custom judge on one row. Returns dict with score, passed, reason, threshold.
    Uses Ollama if OLLAMA_MODEL_NAME (or DEEPEVAL_OLLAMA_MODEL) is set, else OpenAI.
    """
    use_ollama = bool(
        os.environ.get("OLLAMA_MODEL_NAME", "").strip()
        or os.environ.get("DEEPEVAL_OLLAMA_MODEL", "").strip()
    )
    if use_ollama:
        score, reason = judge_with_ollama(judge_input, actual_output, expected_output or criteria, criteria=criteria)
    else:
        score, reason = judge_with_openai(judge_input, actual_output, expected_output or criteria, criteria=criteria)
    passed = score >= threshold
    return {
        "score": score,
        "passed": passed,
        "reason": reason,
        "threshold": threshold,
    }
