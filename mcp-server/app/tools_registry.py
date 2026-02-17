"""
Modular Tools Registry for MCP Server.

This module exposes the available tools as callable functions that can be 
registered with the MCP server or used by the Universal Agent.
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

from agent import get_domain_schema, list_domains, DomainSchema

# Tool Definitions

def get_selection_criteria(domain: str) -> Optional[Dict[str, Any]]:
    """
    MCP Tool: specific tool to retrieve the selection criteria (schema) for a domain.
    Used by agents to know what questions to ask.
    """
    schema = get_domain_schema(domain)
    if not schema:
        return None
    return schema.model_dump()

def get_available_domains() -> List[str]:
    """
    MCP Tool: list all supported domains.
    """
    return list_domains()

# Registry Metadata for LLM discovery
TOOLS_METADATA = [
    {
        "name": "get_selection_criteria",
        "description": "Get the selection criteria (preference slots) for a specific domain (e.g. 'vehicles', 'laptops').",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The domain to get criteria for."
                }
            },
            "required": ["domain"]
        }
    },
    {
        "name": "get_available_domains",
        "description": "Get a list of all supported domains.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]
