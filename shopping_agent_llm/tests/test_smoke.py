"""
End-to-end smoke tests for the LLM shopping agent.

These tests require OPENAI_API_KEY and will make real LLM calls. They do NOT
require a running MCP backend — a FakeMerchant stubs /merchant/search via
monkeypatch on `shopping_agent_llm.tools.merchant_search`.

Cost: ~4 LLM calls per turn × 2–3 turns × 2 scenarios = ~24 cheap
gpt-4o-mini calls per run. Skip with `-m 'not llm'` if you don't want them.

The assertions are structural (did we emit a searchable StructuredQuery with
the right shape?) rather than natural-language exact-match. NL outputs are
inspected via printed reply text.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for LLM smoke tests",
)


@pytest.fixture()
def fake_merchant(monkeypatch):
    from shopping_agent_llm.tests.fake_merchant import FakeMerchant
    from shopping_agent_llm import tools as tools_mod

    fm = FakeMerchant()

    async def _fake_search(query, settings):
        return fm.search(query)

    monkeypatch.setattr(tools_mod, "merchant_search", _fake_search)
    # graph.py does `from shopping_agent_llm.tools import merchant_search`
    # at import time, so patch the graph binding too.
    from shopping_agent_llm import graph as graph_mod

    monkeypatch.setattr(graph_mod, "merchant_search", _fake_search)
    return fm


@pytest.fixture()
def fresh_store(monkeypatch):
    from shopping_agent_llm.harness import session as session_mod
    from shopping_agent_llm.harness.session import InMemorySessionStore

    store = InMemorySessionStore()
    monkeypatch.setattr(session_mod, "_singleton", store)
    return store


@pytest.mark.asyncio
async def test_laptop_conversation_reaches_search(fake_merchant, fresh_store):
    """
    A budget + use-case utterance should be searchable in one or two turns.
    Once searched, the emitted StructuredQuery must (a) have a 'laptops'-ish
    domain, (b) carry price_max_cents in hard_filters, (c) put brand/use_case
    in soft_preferences, not hard.
    """
    from shopping_agent_llm.graph import run_turn

    sid = str(uuid.uuid4())

    r1 = await run_turn(sid, "I need a laptop for coding, budget about $1500")

    # The Interviewer might still ask one clarifier. Give it a second turn.
    if not fake_merchant.queries:
        r2 = await run_turn(sid, "Just show me what you've got, no preference on brand")
        assert fake_merchant.queries, f"no search after 2 turns; reply={r2.reply!r}"

    sq = fake_merchant.queries[-1]
    assert "laptop" in sq.domain, f"domain was {sq.domain!r}"
    # Budget should be a hard filter.
    price_cap = sq.hard_filters.get("price_max_cents") or sq.hard_filters.get("budget")
    assert price_cap, f"no price cap in hard_filters: {sq.hard_filters}"
    # Use case / brand should NOT be hard-filtered by default.
    assert "use_case" not in sq.hard_filters
    assert "brand" not in sq.hard_filters
    # in_stock should be enforced.
    assert sq.hard_filters.get("in_stock") is True


@pytest.mark.asyncio
async def test_domain_agnostic_shoes(fake_merchant, fresh_store):
    """
    Same pipeline handles a totally different domain with no schema changes.
    """
    from shopping_agent_llm.graph import run_turn

    sid = str(uuid.uuid4())

    r1 = await run_turn(sid, "I want red running shoes, size 10, under $150")
    if not fake_merchant.queries:
        r2 = await run_turn(sid, "Any brand is fine, just show me")

    sq = fake_merchant.queries[-1]
    assert any(k in sq.domain for k in ("shoe", "running")), f"domain was {sq.domain!r}"
    # Color should be soft, not hard.
    assert "color" not in sq.hard_filters
    soft = {**sq.soft_preferences}
    assert any(v == "red" or "red" in str(v).lower() for v in soft.values()), (
        f"color=red not in soft_preferences: {soft}"
    )


@pytest.mark.asyncio
async def test_refine_uses_exclude_ids(fake_merchant, fresh_store):
    """
    After a first search, a 'show more' turn should include exclude_ids so the
    merchant can paginate.
    """
    from shopping_agent_llm.graph import run_turn

    sid = str(uuid.uuid4())
    await run_turn(sid, "Show me laptops under $1500, any brand")
    if not fake_merchant.queries:
        await run_turn(sid, "Just show me")

    # We should have at least one search + the first offer batch.
    first_offer_ids = [o.product_id for o in fake_merchant.queries[-1:]][:1]
    assert fake_merchant.queries, "first search didn't land"

    # Now ask for more.
    await run_turn(sid, "Show me more options")
    last_sq = fake_merchant.queries[-1]
    assert last_sq.user_context.get("exclude_ids"), (
        f"exclude_ids missing on refine: {last_sq.user_context}"
    )
