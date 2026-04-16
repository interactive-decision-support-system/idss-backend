"""
Tools exposed to the Interviewer agent.

The only external side-effect the shopping agent can perform is calling the
merchant. Keep this thin — no filtering, no re-ranking, no post-processing
that the merchant should own. The merchant may evolve behind the contract
(per ARCHITECTURE.md) and this wrapper should not grow to compensate.
"""

from __future__ import annotations

from typing import List

import httpx

from shopping_agent_llm.config import Settings
from shopping_agent_llm.contract import Offer, StructuredQuery


class MerchantSearchError(RuntimeError):
    pass


async def merchant_search(
    query: StructuredQuery, settings: Settings
) -> List[Offer]:
    url = f"{settings.mcp_base_url.rstrip('/')}/merchant/search"
    async with httpx.AsyncClient(timeout=settings.request_timeout_s) as client:
        resp = await client.post(url, json=query.model_dump())
        if resp.status_code >= 400:
            raise MerchantSearchError(
                f"/merchant/search returned {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
    return [Offer.model_validate(o) for o in payload]
