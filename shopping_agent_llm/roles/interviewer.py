"""
Interviewer — orchestrator role.

Given the full conversation state and the latest user utterance, decides what
to do this turn: ask a clarifier, run the search pipeline, or handle a
post-search intent (compare/refine/show-more).

This role deliberately does NOT directly produce the user-facing reply when
a search is needed — that's the Presenter's job after results come back.
It returns a typed routing decision and, when the action is ASK_CLARIFIER,
the clarifier text to show.

Domain-agnostic. The prompt gives the model a short rubric for "what makes a
query searchable" rather than any hardcoded slot list.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from shopping_agent_llm.roles._cache import get_agent
from shopping_agent_llm.schema import ConversationState, TurnAction


class InterviewerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ask_clarifier", "search", "compare", "refine", "show_more"] = (
        Field(
            ...,
            description="Next step. 'search' = run extraction + query build + merchant call + presenter.",
        )
    )
    clarifier: Optional[str] = Field(
        None,
        description="Required iff action == 'ask_clarifier'. Short, open-ended question in the user's tone. Do NOT embed options in the text — put them in quick_replies.",
    )
    quick_replies: list[str] = Field(
        default_factory=list,
        description="0–4 short chip labels the UI may render next to the clarifier (e.g. ['gaming', 'work', 'school']). Empty list when not useful.",
    )
    reasoning: str = Field(
        "",
        description="One short sentence explaining why this action was chosen. Observability only; not shown to the user.",
    )


_SYSTEM_PROMPT = """
You are the Interviewer in a domain-agnostic shopping assistant. You do NOT
write the final answer when products are shown — the Presenter does.
Your only job this turn is to choose ONE next action.

Routing rubric:

1. ask_clarifier — pick this when the current preferences are too thin to
   produce useful results. A query is searchable when you have (a) a product
   category or a strong category signal, AND (b) at least one differentiator
   (budget, use case, key feature, brand, size, etc.). If you already have
   enough, do NOT ask another question. Over-asking is the primary failure
   mode of the legacy system — err toward searching.

2. search — pick this on the FIRST turn where the query is searchable per (1).
   Also pick this when the user says things like "just show me", "what do you
   recommend", "go", or otherwise signals they want results.

3. refine — pick this when products were already shown and the user wants a
   variation ("cheaper", "different brand", "more RAM", "in blue").

4. compare — pick this when the user is asking about two or more of the shown
   products relative to each other.

5. show_more — pick this when the user asks for more results without changing
   preferences ("show more", "what else", "next page").

Domain-agnostic principles:
- Do NOT assume a fixed slot schema. The conversation may be about laptops,
  mattresses, running shoes, or groceries — you do not need to know in
  advance which slots matter.
- If the user's message is nonsense, abusive, or clearly off-topic, choose
  ask_clarifier with a short redirect.

Clarifier style:
- One question, conversational, under 20 words.
- Populate quick_replies with 2–4 short chip labels when useful (e.g.
  ["gaming", "work", "school"]). Do NOT stuff them into the clarifier
  text — they render as buttons.
- Never re-ask something already answered in the slots.
"""


_DEFAULT_MODEL = "openai:gpt-4.1"


def _render_state_block(state: ConversationState, utterance: str) -> str:
    lines = [
        f"Current domain guess: {state.domain or '(unknown)'}",
        f"Accumulated preferences: {state.slots or '(none)'}",
        f"Products already shown: {len(state.last_offers)}",
        f"Turn count: {state.turn_count}",
        "",
        "Recent history (last 6 turns):",
    ]
    for t in state.history[-6:]:
        lines.append(f"  {t.role.value}: {t.content}")
    lines.append("")
    lines.append(f"Latest user utterance: {utterance}")
    return "\n".join(lines)


async def run_interviewer(
    state: ConversationState, utterance: str, model: str | None = None
) -> InterviewerDecision:
    agent = get_agent(
        "interviewer",
        model or _DEFAULT_MODEL,
        InterviewerDecision,
        _SYSTEM_PROMPT,
    )
    prompt = _render_state_block(state, utterance)
    result = await agent.run(prompt)
    return result.output
