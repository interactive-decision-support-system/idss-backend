"""
Presenter — offers → natural-language reply.

Replaces the narration, comparison, and refinement-hint logic scattered
across `chat_endpoint.py` (`_build_preference_ack`, bucketing narrative)
and `comparison_agent.py`. The Presenter sees the state, the offers, and
the intent (first-time present / compare / refine / show-more) and writes a
single reply in one LLM call.

Products are NOT re-ranked or filtered here — the merchant already did that.
The Presenter's job is purely conversational.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field
from shopping_agent_llm.contract import Offer
from shopping_agent_llm.roles._cache import get_agent
from shopping_agent_llm.schema import ConversationState


class PresenterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(
        ...,
        description="The full assistant message. Short (<120 words). Narrates the top offers conversationally.",
    )
    suggested_follow_ups: List[str] = Field(
        default_factory=list,
        description="0–4 short chips the UI may render (e.g. 'cheaper', 'more storage'). Not required.",
    )


_BASE_PROMPT = """
You are the Presenter. Write ONE short assistant message.

Rules:

- Ground every claim in the provided offers. Do not invent specs, prices,
  or availability. If you need a field that isn't in the offer, skip it.
- Be conversational, not listy. One short paragraph usually beats bullets.
  Bullets are fine for a compare call.
- Do not dump every offer. Cover the top 2–3 and refer to "and a few more
  options" if there are more.
- Never mention scores, rationale strings, or internal ids to the user.
- Suggested follow-ups should be phrased like user utterances ("cheaper",
  "only Apple", "show more in red"), not like buttons.
"""

_INTENT_ADDENDA = {
    "present": (
        "This is the first batch of results. Confirm the preferences you "
        "heard, then describe the top picks in one short paragraph."
    ),
    "refine": (
        "The user just refined their preferences. Acknowledge the change in "
        "one clause, then describe the new picks."
    ),
    "show_more": (
        "The user asked for more results. Skip any preference acknowledgement "
        "and go straight to describing the new picks."
    ),
    "compare": (
        "The user asked to compare the shown products. Use short bullets "
        "(3–5 lines max) covering the most relevant differences for their "
        "stated use case. End with a one-sentence recommendation."
    ),
}


def _render_offers(offers: List[Offer], max_items: int = 6) -> str:
    lines = []
    for o in offers[:max_items]:
        p = o.product
        price = f"${p.price_cents / 100:.2f}" if p.price_cents else "(no price)"
        bits = [p.name, price]
        if p.brand:
            bits.append(f"brand={p.brand}")
        if p.category:
            bits.append(f"cat={p.category}")
        if p.color:
            bits.append(f"color={p.color}")
        if o.rationale:
            bits.append(f"why={o.rationale[:100]}")
        lines.append(" · ".join(bits))
    return "\n".join(lines) if lines else "(no offers)"


async def run_presenter(
    state: ConversationState,
    offers: List[Offer],
    intent: Literal["present", "refine", "show_more", "compare"],
    model: str | None = None,
) -> PresenterOutput:
    system = _BASE_PROMPT + "\n\n" + _INTENT_ADDENDA[intent]
    agent = get_agent(
        f"presenter_{intent}",
        model or "openai:gpt-4.1",
        PresenterOutput,
        system,
    )
    prompt = (
        f"Slots the user has expressed: {state.slots}\n"
        f"Domain: {state.domain or '(unknown)'}\n"
        f"Offers (top to bottom, merchant-ranked):\n{_render_offers(offers)}\n"
    )
    return (await agent.run(prompt)).output
