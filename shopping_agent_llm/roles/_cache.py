"""
Agent cache.

pydantic_ai.Agent() construction instantiates an OpenAI client, which raises
at import time if OPENAI_API_KEY is missing. We therefore construct agents
lazily — on first use — and cache by (model, role) so subsequent turns don't
pay reconstruction overhead.

Each role calls `get_agent(role_name, model, result_type, system_prompt)`.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, Type

from pydantic_ai import Agent


_CACHE: Dict[Tuple[str, str], Agent] = {}


def get_agent(
    role_name: str,
    model: str,
    result_type: Type[Any],
    system_prompt: str,
) -> Agent:
    key = (role_name, model)
    agent = _CACHE.get(key)
    if agent is None:
        agent = Agent(
            model=model, result_type=result_type, system_prompt=system_prompt
        )
        _CACHE[key] = agent
    return agent
