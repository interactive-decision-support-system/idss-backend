"""
Typed state for the LLM-first shopping agent.

All state that flows between roles is pinned here. Keep this file authoritative
— every role consumes or produces these types. Pydantic v2, no Optional-laden
kitchen sinks: model what's actually needed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from shopping_agent_llm.contract import Offer, StructuredQuery


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    content: str


class PreferenceDelta(BaseModel):
    """
    Output of the Extractor. Expresses what changed on this turn — not the full
    slot set. The Interviewer merges it into ConversationState.
    """
    model_config = ConfigDict(extra="forbid")

    domain_guess: Optional[str] = Field(
        None,
        description="Free-form domain string if newly inferred or updated (e.g. 'laptops', 'running_shoes'). Omit if unchanged.",
    )
    add_or_update: Dict[str, Any] = Field(
        default_factory=dict,
        description="Slot key → value pairs the user asserted or refined this turn. Free-form keys.",
    )
    remove: List[str] = Field(
        default_factory=list,
        description="Slot keys the user explicitly retracted (e.g. 'no longer care about brand').",
    )
    intent: Literal[
        "provide_preference",
        "refine",
        "compare",
        "ask_clarification",
        "show_more",
        "off_topic",
    ] = Field(
        ...,
        description="Interpreted user intent for this turn. The Interviewer uses this to route.",
    )
    notes: str = Field(
        "",
        description="Free-form observation the Extractor wants to pass up (e.g. 'user seems impatient, wants to skip to results').",
    )


class ConversationState(BaseModel):
    """
    Per-session durable state. Serializable so a session store can round-trip it.
    """
    model_config = ConfigDict(extra="forbid")

    session_id: str
    history: List[Turn] = Field(default_factory=list)
    domain: Optional[str] = None
    slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="Accumulated preferences, domain-agnostic keys.",
    )
    shown_product_ids: List[str] = Field(
        default_factory=list,
        description="Populates StructuredQuery.user_context.exclude_ids for pagination and refine.",
    )
    last_offers: List[Offer] = Field(
        default_factory=list,
        description="Offers presented in the most recent search turn. Enables compare / 'the second one' references.",
    )
    turn_count: int = 0

    def append_user(self, content: str) -> None:
        self.history.append(Turn(role=Role.USER, content=content))
        self.turn_count += 1

    def append_assistant(self, content: str) -> None:
        self.history.append(Turn(role=Role.ASSISTANT, content=content))

    def apply_delta(self, delta: PreferenceDelta) -> None:
        if delta.domain_guess:
            self.domain = delta.domain_guess
        for k, v in delta.add_or_update.items():
            self.slots[k] = v
        for k in delta.remove:
            self.slots.pop(k, None)


class TurnAction(str, Enum):
    ASK_CLARIFIER = "ask_clarifier"
    SEARCH = "search"
    PRESENT = "present"
    COMPARE = "compare"
    REFINE = "refine"


class TurnResult(BaseModel):
    """
    Final output of a single turn. The API shim serializes this for the client.
    """
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(..., description="Assistant message to show the user.")
    action: TurnAction
    offers: List[Offer] = Field(default_factory=list)
    quick_replies: List[str] = Field(
        default_factory=list,
        description="Chip labels for the UI. From Interviewer on clarifier turns, from Presenter on result turns.",
    )
    structured_query: Optional[StructuredQuery] = Field(
        None,
        description="The query that produced `offers`, if any. Echoed for observability.",
    )
    state: ConversationState
    latency_ms: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-role wall-clock timings. 'total' is the turn total.",
    )
