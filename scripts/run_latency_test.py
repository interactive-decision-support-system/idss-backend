#!/usr/bin/env python3
"""
Latency test: calls /chat with diverse queries, saves timings_ms to JSONL.

Usage:
    python scripts/run_latency_test.py
    python scripts/parse_latency_logs.py latency_results.jsonl
"""
import json
import uuid
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://localhost:8001"
OUT = Path(__file__).parent.parent / "latency_results.jsonl"


def chat(message: str, session_id: str = None, k: int = None) -> dict | None:
    body: dict = {"message": message}
    if session_id:
        body["session_id"] = session_id
    if k is not None:
        body["k"] = k
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}/chat", data=data,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            result["_client_ms"] = (time.perf_counter() - t0) * 1000
            return result
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


# ---------------------------------------------------------------------------
# Scenario 1: k=0 (skip interview → straight to search/format timings)
# ---------------------------------------------------------------------------
SKIP_INTERVIEW = [
    "I want a family SUV under $40k",
    "Looking for a fuel-efficient sedan under $30k",
    "Need a truck for towing, budget around $50k",
    "Sports car with great performance under $60k",
    "Electric vehicle with long range",
    "I need a laptop for programming under $1500",
    "Gaming laptop with RTX GPU under $2000",
    "Lightweight laptop for travel, under $1000",
    "MacBook or Dell laptop for design work",
    "Budget laptop under $500",
    "Looking for science fiction novels",
    "Fantasy books for adults",
    "Books on machine learning and AI",
    "Mystery thriller novels under $20",
    "Self-help books on productivity",
]

# ---------------------------------------------------------------------------
# Scenario 2: Full interview flows (domain detect → criteria → question → recs)
# Each list is a sequence of messages in one session (natural k=3 interview)
# ---------------------------------------------------------------------------
INTERVIEW_FLOWS = [
    ["Cars",       "I prefer SUV",        "budget around $35k",    "any fuel type"],
    ["Cars",       "sedan",               "Toyota or Honda",        "under $28000"],
    ["Laptops",    "programming and video editing", "under $2000", "16GB RAM"],
    ["Laptops",    "gaming",              "good GPU",               "around $1500"],
    ["Books",      "science fiction",     "under $25"],
    ["Books",      "history and biography"],
    ["I need a car for commuting",        "Honda or Toyota",        "hybrid preferred"],
    ["looking for a new laptop",          "lightweight for travel",  "under $1200"],
    ["I want to buy some books",          "mystery thriller genre"],
    # Longer, more realistic multi-turn
    ["Cars",       "family car with 7 seats", "budget $40k-$55k",   "gasoline ok"],
    ["Laptops",    "student laptop",      "under $800",             "long battery life"],
]


def run():
    results = []
    total_queries = 0

    # --- Scenario 1: k=0 recommendation queries ---
    print(f"\n=== Scenario 1: {len(SKIP_INTERVIEW)} recommendation queries (k=0) ===")
    for msg in SKIP_INTERVIEW:
        sid = str(uuid.uuid4())
        print(f"  ▶ {msg[:60]}")
        r = chat(msg, session_id=sid, k=0)
        if r and r.get("timings_ms") is not None:
            entry = {
                "timings_ms": r["timings_ms"],
                "session_id": r.get("session_id", sid),
                "response_type": r.get("response_type"),
            }
            results.append(entry)
            t = r["timings_ms"] or {}
            search = t.get("ecommerce_search_ms") or t.get("vehicle_search_ms") or 0
            print(f"    backend={t.get('total_backend_ms', 0):.0f}ms  "
                  f"domain={t.get('domain_detection_ms', 0):.0f}ms  "
                  f"search={search:.0f}ms")
        total_queries += 1
        time.sleep(0.4)

    # --- Scenario 2: Multi-turn interview flows ---
    print(f"\n=== Scenario 2: {len(INTERVIEW_FLOWS)} interview sessions ===")
    for flow in INTERVIEW_FLOWS:
        sid = str(uuid.uuid4())
        label = " → ".join(flow[:2])
        print(f"  ▶ Session [{label}]")
        for msg in flow:
            r = chat(msg, session_id=sid)
            if r and r.get("timings_ms") is not None:
                entry = {
                    "timings_ms": r["timings_ms"],
                    "session_id": r.get("session_id", sid),
                    "response_type": r.get("response_type"),
                }
                results.append(entry)
                t = r["timings_ms"] or {}
                domain_ms = t.get("domain_detection_ms", 0) or 0
                criteria_ms = t.get("criteria_extraction_ms", 0) or 0
                qgen_ms = t.get("question_generation_ms", 0) or 0
                search_ms = t.get("ecommerce_search_ms") or t.get("vehicle_search_ms") or 0
                print(f"      '{msg[:30]:<30}' "
                      f"type={r.get('response_type','?'):<18} "
                      f"dom={domain_ms:.0f}ms crit={criteria_ms:.0f}ms "
                      f"qgen={qgen_ms:.0f}ms search={search_ms:.0f}ms "
                      f"total={t.get('total_backend_ms', 0):.0f}ms")
            elif not r:
                print(f"      '{msg[:30]}' → FAILED")
                break
            total_queries += 1
            time.sleep(0.35)

    # --- Save results ---
    with open(OUT, "w") as f:
        for entry in results:
            f.write(json.dumps(entry) + "\n")

    print(f"\n✓ {len(results)} entries from {total_queries} queries → {OUT}")
    return len(results)


if __name__ == "__main__":
    n = run()
    if n == 0:
        print("No data collected — is the backend running on port 8001?")
    else:
        print(f"\nNow run:  python scripts/parse_latency_logs.py {OUT}")
