"""
Session state store.

In-memory by default. Swap the backend by subclassing SessionStore — a Redis
variant is sketched at the bottom for parity with the legacy agent's
`mcp:session:*` keys, but not wired by default since the prototype targets
latency-measurable local runs first.
"""

from __future__ import annotations

from typing import Dict, Optional

from shopping_agent_llm.schema import ConversationState


class SessionStore:
    def get(self, session_id: str) -> Optional[ConversationState]:
        raise NotImplementedError

    def put(self, state: ConversationState) -> None:
        raise NotImplementedError

    def get_or_create(self, session_id: str) -> ConversationState:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        fresh = ConversationState(session_id=session_id)
        self.put(fresh)
        return fresh


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._store: Dict[str, ConversationState] = {}

    def get(self, session_id: str) -> Optional[ConversationState]:
        return self._store.get(session_id)

    def put(self, state: ConversationState) -> None:
        self._store[state.session_id] = state


_singleton: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _singleton
    if _singleton is None:
        _singleton = InMemorySessionStore()
    return _singleton


# RedisSessionStore sketch — wire when running under a real worker:
#
# class RedisSessionStore(SessionStore):
#     def __init__(self, client): self._r = client
#     def get(self, sid):
#         raw = self._r.get(f"sa_llm:session:{sid}")
#         return ConversationState.model_validate_json(raw) if raw else None
#     def put(self, state):
#         self._r.setex(f"sa_llm:session:{state.session_id}", 3600,
#                       state.model_dump_json())
