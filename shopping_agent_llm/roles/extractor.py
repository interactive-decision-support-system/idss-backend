"""
Extractor — utterance → PreferenceDelta.

Replaces `universal_agent.get_search_filters()`, the slot-name alias maps,
the brand-value alias maps, and the query rewriter. The LLM is responsible
for normalization — "mac" → "Apple", "1TB RAM" (typo) → "1TB SSD" — based
on a short rubric in the prompt, not on hardcoded tables.

Kept deliberately small. The model sees the current slots so it can emit
deltas (add/update/remove) rather than re-describing the whole state.
"""

from __future__ import annotations

from shopping_agent_llm.roles._cache import get_agent
from shopping_agent_llm.schema import ConversationState, PreferenceDelta


_SYSTEM_PROMPT = """
You are the Extractor in a shopping assistant. Output a typed PreferenceDelta
describing ONLY what changed this turn.

Rules:

- Slot keys are free-form, snake_case. Pick the most natural key for the
  attribute the user expressed. Examples: "budget_usd", "use_case",
  "brand", "color", "size", "storage_gb", "battery_life", "material".
- Normalize informal language into canonical forms. Examples:
  "a mac" → brand = "Apple";
  "around 1500 bucks" → budget_usd = 1500;
  "for gaming" → use_case = "gaming";
  "red-ish" → color = "red".
- If the user corrects or retracts a previous slot ("actually, not Apple"),
  put its key in `remove`.
- `domain_guess` should be a short free-form noun phrase
  (e.g. "laptops", "running_shoes"). Set it only when your current guess
  differs from the `domain` already on the state.
- `intent` must reflect this turn's utterance, not historical state:
   * provide_preference — user is giving/refining slots.
   * refine — user wants a variation on already-shown products.
   * compare — user is comparing two or more shown products.
   * ask_clarification — user is asking YOU a question about the products.
   * show_more — user wants more results, no new preferences.
   * off_topic — unrelated / abusive / nonsense.
- Do NOT invent preferences. If the utterance is thin, emit an empty delta
  with the appropriate intent.
"""


_DEFAULT_MODEL = "openai:gpt-4.1-mini"


def _render_prompt(state: ConversationState, utterance: str) -> str:
    return (
        f"Current domain: {state.domain or '(unknown)'}\n"
        f"Current slots: {state.slots or '(none)'}\n"
        f"Products shown so far: {len(state.last_offers)}\n"
        f"User utterance: {utterance}\n"
    )


async def run_extractor(
    state: ConversationState, utterance: str, model: str | None = None
) -> PreferenceDelta:
    agent = get_agent(
        "extractor",
        model or _DEFAULT_MODEL,
        PreferenceDelta,
        _SYSTEM_PROMPT,
    )
    result = await agent.run(_render_prompt(state, utterance))
    return result.output
