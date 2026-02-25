#!/usr/bin/env python3
"""
Latency benchmark for the IDSS Merchant Agent.
Measures and prints a poster-ready table of response times for:
  1. Feature-based search  (GET /api/search-products)
  2. Complex query — best value  (agent chat: "get best value")
  3. Complex query — similar items  (agent chat: "see similar items")
  4. Inventory check  (GET /api/get-product)
  5. Cart add  (POST /api/action/add-to-cart)
  6. Checkout  (POST /api/action/checkout)

Usage:
  # Start the server first:  ./start_all_local.sh
  # Then run:
  python scripts/latency_table.py [--base-url http://localhost:8001] [--runs 5]
"""
import argparse
import json
import statistics
import time
import uuid
import httpx

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_BASE = "http://localhost:8001"
DEFAULT_RUNS = 5

SEARCH_QUERIES = [
    # (label, endpoint, payload, category)
    (
        "Feature-based search\n(semantic query, no filters)",
        "/api/search-products",
        {"query": "gaming laptop with dedicated GPU under 1500", "limit": 6},
        "POST",
    ),
    (
        "Feature-based search\n(with explicit filters)",
        "/api/search-products",
        {"query": "laptop", "filters": {"category": "Electronics", "price_max": 1200}, "limit": 6},
        "POST",
    ),
    (
        "Get single product\n(inventory + details)",
        None,  # resolved dynamically after first search
        None,
        "GET_PRODUCT",
    ),
    (
        "Agent chat — initial query\n(agentic, LLM-driven)",
        "/chat",
        {"message": "I need a thin and light laptop for college under $1000", "session_id": None},
        "POST",
    ),
    (
        "Complex query — best value\n(KG + scoring + UCP)",
        "/chat",
        {"message": "Get best value", "session_id": None},
        "POST",
    ),
    (
        "Complex query — similar items\n(embedding + KG relationships)",
        "/chat",
        {"message": "See similar items", "session_id": None},
        "POST",
    ),
]


# --------------------------------------------------------------------------- #
# Benchmark runner
# --------------------------------------------------------------------------- #
def run_benchmark(base_url: str, runs: int) -> list[dict]:
    results = []
    client = httpx.Client(base_url=base_url, timeout=30.0)

    # ---- seed a session so "best value" / "similar items" have context ----
    session_id = str(uuid.uuid4())
    seed_resp = client.post("/chat", json={
        "message": "Show me gaming laptops for beginners under $1500",
        "session_id": session_id,
    })
    seed_data = seed_resp.json() if seed_resp.status_code == 200 else {}
    session_id = seed_data.get("session_id", session_id)

    # grab a product id for the inventory check
    first_product_id = None
    search_seed = client.post("/api/search-products", json={
        "query": "laptop", "limit": 1
    })
    if search_seed.status_code == 200:
        prods = search_seed.json().get("data", {}).get("products", [])
        if prods:
            first_product_id = prods[0].get("product_id")

    for label, endpoint, payload, method in SEARCH_QUERIES:
        times_ms: list[float] = []
        errors = 0

        for _ in range(runs):
            try:
                t0 = time.perf_counter()

                if method == "GET_PRODUCT":
                    if not first_product_id:
                        errors += 1
                        continue
                    r = client.post("/api/get-product", json={"product_id": first_product_id})
                elif method == "POST":
                    # inject session_id for chat requests
                    body = dict(payload)
                    if "session_id" in body:
                        body["session_id"] = session_id
                    r = client.post(endpoint, json=body)
                else:
                    errors += 1
                    continue

                elapsed = (time.perf_counter() - t0) * 1000
                if r.status_code in (200, 201):
                    times_ms.append(elapsed)
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                print(f"  [WARN] {label!r}: {e}")

        if times_ms:
            results.append({
                "label": label,
                "runs": len(times_ms),
                "errors": errors,
                "mean_ms": statistics.mean(times_ms),
                "median_ms": statistics.median(times_ms),
                "p95_ms": sorted(times_ms)[int(0.95 * len(times_ms))] if len(times_ms) >= 2 else times_ms[-1],
                "min_ms": min(times_ms),
                "max_ms": max(times_ms),
            })
        else:
            results.append({
                "label": label, "runs": 0, "errors": errors,
                "mean_ms": None, "median_ms": None, "p95_ms": None,
                "min_ms": None, "max_ms": None,
            })

    client.close()
    return results


# --------------------------------------------------------------------------- #
# Pretty-print table
# --------------------------------------------------------------------------- #
def print_table(results: list[dict]) -> None:
    COL = [35, 8, 10, 10, 10]
    header = ["Operation", "Runs", "Mean (ms)", "Median", "P95 (ms)"]
    sep = "+" + "+".join("-" * (w + 2) for w in COL) + "+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in COL) + " |"

    print("\n")
    print("=" * 70)
    print("  IDSS Merchant Agent — Latency Benchmark")
    print("=" * 70)
    print(sep)
    print(fmt.format(*header))
    print(sep)

    for r in results:
        label_short = r["label"].replace("\n", " ")[:COL[0]]
        if r["mean_ms"] is None:
            row = [label_short, str(r["runs"]), "ERR", "ERR", "ERR"]
        else:
            row = [
                label_short,
                str(r["runs"]),
                f"{r['mean_ms']:.0f}",
                f"{r['median_ms']:.0f}",
                f"{r['p95_ms']:.0f}",
            ]
        print(fmt.format(*row))

    print(sep)
    print()
    print("  Target: < 3 000 ms for all merchant-agent operations")
    print()

    # also write JSON for poster
    out_path = "latency_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Full results written to {out_path}")
    print()


# --------------------------------------------------------------------------- #
# Analyze existing jsonl log file
# --------------------------------------------------------------------------- #
def analyze_log(log_path: str) -> None:
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    with open(log_path) as f:
        for line in f:
            try:
                entry = json.loads(line)
                cat = entry.get("category", "other")
                dur = entry.get("duration_ms")
                if dur is not None:
                    buckets[cat].append(dur)
            except Exception:
                pass
    if not buckets:
        print("No data in log file.")
        return

    print("\n")
    print("=" * 70)
    print(f"  Live log analysis: {log_path}")
    print("=" * 70)
    for cat, times in sorted(buckets.items()):
        if not times:
            continue
        print(f"  {cat:<25}  n={len(times):>4}  "
              f"mean={statistics.mean(times):>7.1f}ms  "
              f"median={statistics.median(times):>7.1f}ms  "
              f"p95={sorted(times)[int(0.95*len(times))]:>7.1f}ms")
    print()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="IDSS latency benchmark")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--analyze-log", default=None,
                        help="Path to backend_latency_logs.jsonl to analyze (skip HTTP benchmark)")
    args = parser.parse_args()

    if args.analyze_log:
        analyze_log(args.analyze_log)
        return

    print(f"\nRunning {args.runs} iterations per operation against {args.base_url} ...")
    results = run_benchmark(args.base_url, args.runs)
    print_table(results)


if __name__ == "__main__":
    main()
