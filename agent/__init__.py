"""
Agent package for IDSS — the interviewing brain.

Contains domain detection, criteria extraction, question generation,
and recommendation explanation logic. All LLM calls live here.

Independent of the MCP server layer (HTTP, database, caching).
"""
from .universal_agent import UniversalAgent, AgentState, DEFAULT_MAX_QUESTIONS
from .domain_registry import (
    get_domain_schema, list_domains, DomainSchema,
    SlotPriority, PreferenceSlot, DOMAIN_REGISTRY,
)
from .chat_endpoint import (
    ChatRequest, ChatResponse, process_chat,
)
