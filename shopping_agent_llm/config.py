from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    mcp_base_url: str
    interviewer_model: str
    extractor_model: str
    query_builder_model: str
    presenter_model: str
    request_timeout_s: float
    default_top_k: int


def load_settings() -> Settings:
    # Tiered model stack. All roles on gpt-4.1 family (Chat Completions) for
    # structured-output stability; reasoning-model swap-in is available per
    # role via env var once we're ready to validate o-series behavior.
    #
    # To try reasoning on the judgment-heavy roles:
    #   export SA_LLM_INTERVIEWER_MODEL=openai:o4-mini
    #   export SA_LLM_QUERY_BUILDER_MODEL=openai:o4-mini
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        mcp_base_url=os.environ.get("MCP_BASE_URL", "http://localhost:8001"),
        interviewer_model=os.environ.get("SA_LLM_INTERVIEWER_MODEL", "openai:gpt-4.1"),
        extractor_model=os.environ.get("SA_LLM_EXTRACTOR_MODEL", "openai:gpt-4.1-mini"),
        query_builder_model=os.environ.get("SA_LLM_QUERY_BUILDER_MODEL", "openai:gpt-4.1"),
        presenter_model=os.environ.get("SA_LLM_PRESENTER_MODEL", "openai:gpt-4.1"),
        request_timeout_s=float(os.environ.get("SA_LLM_TIMEOUT_S", "30")),
        default_top_k=int(os.environ.get("SA_LLM_DEFAULT_TOP_K", "10")),
    )
