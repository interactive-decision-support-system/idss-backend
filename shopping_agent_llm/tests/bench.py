"""
Latency micro-bench.

Runs one scripted conversation against the FakeMerchant so latency reflects
LLM wall-time (not merchant retrieval). Writes per-turn + per-role timings
to BENCH.md-style stdout.

    python -m shopping_agent_llm.tests.bench
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import uuid

from shopping_agent_llm.harness.session import InMemorySessionStore
from shopping_agent_llm import graph as graph_mod
from shopping_agent_llm import tools as tools_mod
from shopping_agent_llm.graph import run_turn
from shopping_agent_llm.harness import session as session_mod
from shopping_agent_llm.tests.fake_merchant import FakeMerchant


SCRIPT = [
    "I need a laptop for coding, budget around $1500",
    "No strong brand preference, just a good keyboard",
    "Show me cheaper options",
    "Compare the top two",
]


async def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — skipping bench.")
        return 1

    fm = FakeMerchant()

    async def _fake_search(q, s):
        return fm.search(q)

    tools_mod.merchant_search = _fake_search
    graph_mod.merchant_search = _fake_search

    store = InMemorySessionStore()
    session_mod._singleton = store

    sid = str(uuid.uuid4())
    per_turn_totals = []
    role_sums: dict[str, list[float]] = {}

    print(f"# bench — {len(SCRIPT)} turns, FakeMerchant\n")
    for i, utterance in enumerate(SCRIPT, 1):
        r = await run_turn(sid, utterance)
        total = r.latency_ms.get("total", 0)
        per_turn_totals.append(total)
        print(f"## turn {i}: {utterance!r}")
        print(f"  action: {r.action.value}")
        print(f"  reply:  {r.reply[:140]!r}")
        print(f"  latency_ms: {r.latency_ms}")
        for k, v in r.latency_ms.items():
            role_sums.setdefault(k, []).append(v)
        print()

    print("# summary")
    print(f"  total p50: {statistics.median(per_turn_totals):.0f} ms")
    print(f"  total max: {max(per_turn_totals):.0f} ms")
    for k, vs in sorted(role_sums.items()):
        print(f"  {k}: p50={statistics.median(vs):.0f} max={max(vs):.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
