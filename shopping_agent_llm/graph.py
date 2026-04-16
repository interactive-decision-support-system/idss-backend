"""
Turn orchestration.

Wires the four roles into one turn. Orchestration lives in plain Python (not
the LLM) so that:
  - routing is deterministic given the Interviewer's decision,
  - per-role latency is measurable,
  - the LLM doesn't burn round-trips chaining through tool calls.

One turn shape:

    user utterance
        │
        ▼
    Interviewer ──► decision
        │
        ├── ask_clarifier: append reply, done
        │
        ├── search / refine: Extractor → merge into state → QueryBuilder
        │                     → merchant_search → Presenter
        │
        ├── show_more: QueryBuilder (unchanged slots) → merchant_search
        │              → Presenter(show_more)
        │
        └── compare: Presenter(compare) over state.last_offers (no search)

The harness timer wraps each sub-step so BENCH.md numbers fall out for free.
"""

from __future__ import annotations

from shopping_agent_llm.config import Settings, load_settings
from shopping_agent_llm.harness import LatencyTimer, SessionStore, get_session_store
from shopping_agent_llm.roles import (
    run_extractor,
    run_interviewer,
    run_presenter,
    run_query_builder,
)
from shopping_agent_llm.schema import (
    ConversationState,
    TurnAction,
    TurnResult,
)
from shopping_agent_llm.tools import merchant_search


async def run_turn(
    session_id: str,
    utterance: str,
    store: SessionStore | None = None,
    settings: Settings | None = None,
) -> TurnResult:
    settings = settings or load_settings()
    store = store or get_session_store()
    timer = LatencyTimer()

    state = store.get_or_create(session_id)
    state.append_user(utterance)

    with timer.section("interviewer"):
        decision = await run_interviewer(
            state, utterance, model=settings.interviewer_model
        )

    if decision.action == "ask_clarifier":
        reply = decision.clarifier or "Could you tell me a bit more about what you're looking for?"
        state.append_assistant(reply)
        store.put(state)
        return TurnResult(
            reply=reply,
            action=TurnAction.ASK_CLARIFIER,
            offers=[],
            quick_replies=decision.quick_replies,
            structured_query=None,
            state=state,
            latency_ms=timer.finalize(),
        )

    if decision.action == "compare":
        if not state.last_offers:
            reply = "I haven't shown any products yet — want me to search first?"
            state.append_assistant(reply)
            store.put(state)
            return TurnResult(
                reply=reply,
                action=TurnAction.ASK_CLARIFIER,
                offers=[],
                structured_query=None,
                state=state,
                latency_ms=timer.finalize(),
            )
        with timer.section("presenter"):
            presented = await run_presenter(
                state, state.last_offers, intent="compare", model=settings.presenter_model
            )
        state.append_assistant(presented.reply)
        store.put(state)
        return TurnResult(
            reply=presented.reply,
            action=TurnAction.COMPARE,
            offers=state.last_offers,
            quick_replies=presented.suggested_follow_ups,
            structured_query=None,
            state=state,
            latency_ms=timer.finalize(),
        )

    # search / refine / show_more all hit the merchant.
    if decision.action != "show_more":
        with timer.section("extractor"):
            delta = await run_extractor(
                state, utterance, model=settings.extractor_model
            )
        state.apply_delta(delta)

    with timer.section("query_builder"):
        query = await run_query_builder(
            state, top_k=settings.default_top_k, model=settings.query_builder_model
        )

    with timer.section("merchant_search"):
        offers = await merchant_search(query, settings)

    state.last_offers = offers
    state.shown_product_ids.extend(o.product_id for o in offers)

    intent = "show_more" if decision.action == "show_more" else (
        "refine" if decision.action == "refine" else "present"
    )
    with timer.section("presenter"):
        presented = await run_presenter(
            state, offers, intent=intent, model=settings.presenter_model
        )

    state.append_assistant(presented.reply)
    store.put(state)

    action_map = {
        "search": TurnAction.SEARCH,
        "refine": TurnAction.REFINE,
        "show_more": TurnAction.PRESENT,
    }
    return TurnResult(
        reply=presented.reply,
        action=action_map[decision.action],
        offers=offers,
        quick_replies=presented.suggested_follow_ups,
        structured_query=query,
        state=state,
        latency_ms=timer.finalize(),
    )
