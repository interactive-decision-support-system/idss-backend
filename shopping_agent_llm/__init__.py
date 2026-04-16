"""
Experimental LLM-first shopping agent. See README.md.

Top-level is intentionally lazy so `import shopping_agent_llm` doesn't pull
pydantic_ai unless a caller actually uses a role. This lets callers import
the pure data types (ConversationState, StructuredQuery re-export) in
environments where the heavy deps aren't installed.
"""

from shopping_agent_llm.schema import ConversationState, TurnResult

__all__ = ["ConversationState", "TurnResult", "run_turn"]


def run_turn(*args, **kwargs):
    from shopping_agent_llm.graph import run_turn as _run_turn

    return _run_turn(*args, **kwargs)
