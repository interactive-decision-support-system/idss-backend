"""
QueryBuilder — ConversationState → StructuredQuery.

Replaces the hardcoded `HARD_KEYS = {category, product_type, in_stock, ...}`
set in `agent/chat_endpoint.py`. The model sees the full slot dict and
decides, per slot, whether it's a hard filter or a soft preference based on
a short rubric.

The output is the contract object — no translation layer, no legacy slot
alias map. Extra keys allowed on both hard/soft (StructuredQuery uses
Dict[str, Any]), so schema evolution on the merchant side does not block
this role.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field
from shopping_agent_llm.contract import StructuredQuery
from shopping_agent_llm.roles._cache import get_agent
from shopping_agent_llm.schema import ConversationState


class _QueryDraft(BaseModel):
    """
    Internal draft — lets the LLM emit the fields of StructuredQuery without
    having to model `top_k` (we set that from settings).
    """
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(..., description="Lowercase vertical slug. Pick from the slots if unsure.")
    hard_filters: Dict[str, Any] = Field(default_factory=dict)
    soft_preferences: Dict[str, Any] = Field(default_factory=dict)
    user_context: Dict[str, Any] = Field(default_factory=dict)


_SYSTEM_PROMPT = """
You are the QueryBuilder. Convert the conversation state into a StructuredQuery
for the merchant agent. The merchant owns retrieval — you only decide what to
send and how to split hard vs soft.

Hard-vs-soft rubric (domain-agnostic):

- HARD (must-match) — the user has asserted this as a CONSTRAINT, violating
  it would be wrong. Typical hard filters:
   * price caps / floors / budgets (price_max_cents, price_min_cents,
     budget).
   * in_stock = true (implicit — always set this).
   * category / product_type when the user was explicit
     ("I want a laptop", not "maybe a laptop").
   * size / dimension when the user gave a specific value they must fit
     (shoe size, screen size, mattress size, bottle volume, …).

- SOFT (score against) — the user expressed a preference but a close match
  should still surface. Typical soft:
   * brand, color, style, aesthetic, material, finish.
   * use_case, activity, audience ("for gaming", "for my kid").
   * nice-to-have features ("good battery", "quiet", "lightweight").
   * any slot where you're not certain it's a hard constraint.

When in doubt → soft. Over-hard-filtering is the primary failure mode:
it returns zero results and the user has to ask again.

Other rules:

- `domain` is lowercase and concise. Use the conversation's domain if set,
  else pick the most natural single word from the slots.
- Budget: if the user gave a number in USD, include BOTH
  `budget` (the raw number) and `price_max_cents` (budget * 100) in hard_filters.
  The merchant may key off either depending on its schema maturity.
- `user_context`: include a `query` key with a short natural-language
  summary of what the user is looking for (one sentence, used for KG/vector
  seeding). Include nothing else unless there's a specific reason.
- Never put PII in user_context.
- Do NOT include slots that are empty/None.
"""


_DEFAULT_MODEL = "openai:gpt-4.1"


def _render_prompt(state: ConversationState) -> str:
    return (
        f"Domain hint: {state.domain or '(unknown)'}\n"
        f"Slots: {state.slots}\n"
        f"Products already shown (exclude by id): {len(state.shown_product_ids)}\n"
    )


async def run_query_builder(
    state: ConversationState, top_k: int, model: str | None = None
) -> StructuredQuery:
    agent = get_agent(
        "query_builder",
        model or _DEFAULT_MODEL,
        _QueryDraft,
        _SYSTEM_PROMPT,
    )
    draft = (await agent.run(_render_prompt(state))).data

    # Always enforce in_stock and exclude_ids server-contract-style; the LLM
    # shouldn't have to remember these every time.
    hard = dict(draft.hard_filters)
    hard.setdefault("in_stock", True)

    ctx = dict(draft.user_context)
    if state.shown_product_ids:
        ctx["exclude_ids"] = list(state.shown_product_ids)

    return StructuredQuery(
        domain=draft.domain.lower().strip(),
        hard_filters=hard,
        soft_preferences=draft.soft_preferences,
        user_context=ctx,
        top_k=top_k,
    )
