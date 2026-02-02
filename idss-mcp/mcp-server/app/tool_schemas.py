"""
Canonical Tool Schema Definition for Multi-LLM Support.

Defines MCP tools in a universal format that can be translated to:
- OpenAI function calling format
- Google Gemini function declarations
- Claude tool use format
- Standard MCP format

Each tool definition includes:
- name: Tool name
- description: What the tool does
- parameters: Input schema (JSON Schema format)
- returns: Output schema
"""

from typing import Dict, List, Any
from enum import Enum


class ToolCategory(str, Enum):
    """Tool categories for organization."""
    DISCOVERY = "discovery"
    DETAIL = "detail"
    EXECUTION = "execution"


# Canonical tool definitions
# These are vendor-neutral and get translated to provider-specific formats

TOOL_SEARCH_PRODUCTS = {
    "name": "search_products",
    "category": ToolCategory.DISCOVERY,
    "description": "Search for products in the catalog using free-text query and/or structured filters. Returns a list of matching products with basic information (ID, name, price, availability).",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text search query to match against product name, description, category, or brand. Optional."
            },
            "filters": {
                "type": "object",
                "description": "Structured filters to narrow results. Optional.",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Product category filter"
                    },
                    "brand": {
                        "type": "string",
                        "description": "Brand name filter"
                    },
                    "price_min": {
                        "type": "number",
                        "description": "Minimum price in dollars"
                    },
                    "price_max": {
                        "type": "number",
                        "description": "Maximum price in dollars"
                    }
                }
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (1-100)",
                "minimum": 1,
                "maximum": 100,
                "default": 10
            },
            "cursor": {
                "type": "string",
                "description": "Pagination cursor from previous response. Optional."
            }
        },
        "required": []
    },
    "returns": {
        "type": "object",
        "description": "Search results with product summaries and pagination"
    }
}

TOOL_GET_PRODUCT = {
    "name": "get_product",
    "category": ToolCategory.DETAIL,
    "description": "Get detailed information about a specific product by its ID. Returns full product details including description, specifications, and metadata. IMPORTANT: Always use the product_id from search results, never product names.",
    "parameters": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "string",
                "description": "Unique product identifier (from search results). REQUIRED."
            },
            "fields": {
                "type": "array",
                "description": "Specific fields to return (field projection for efficiency). If omitted, returns all fields. Optional.",
                "items": {
                    "type": "string",
                    "enum": ["name", "description", "price_cents", "category", "brand", "available_qty", "metadata", "provenance"]
                }
            }
        },
        "required": ["product_id"]
    },
    "returns": {
        "type": "object",
        "description": "Full product details or NOT_FOUND status"
    }
}

TOOL_ADD_TO_CART = {
    "name": "add_to_cart",
    "category": ToolCategory.EXECUTION,
    "description": "Add a product to a shopping cart. Creates cart if it doesn't exist. Validates product availability before adding. CRITICAL: Only accepts product_id, never product names (IDs-only rule).",
    "parameters": {
        "type": "object",
        "properties": {
            "cart_id": {
                "type": "string",
                "description": "Cart identifier. Use a consistent ID across multiple add_to_cart calls. REQUIRED."
            },
            "product_id": {
                "type": "string",
                "description": "Product identifier (from search/get_product). REQUIRED. Never use product name!"
            },
            "qty": {
                "type": "integer",
                "description": "Quantity to add. Must be at least 1. REQUIRED.",
                "minimum": 1
            }
        },
        "required": ["cart_id", "product_id", "qty"]
    },
    "returns": {
        "type": "object",
        "description": "Updated cart contents or constraint (OUT_OF_STOCK, NOT_FOUND)"
    }
}

TOOL_CHECKOUT = {
    "name": "checkout",
    "category": ToolCategory.EXECUTION,
    "description": "Complete checkout for a cart. Validates all items are in stock, creates an order, and decrements inventory. IMPORTANT: IDs-only for cart, payment method, and address.",
    "parameters": {
        "type": "object",
        "properties": {
            "cart_id": {
                "type": "string",
                "description": "Cart identifier (from add_to_cart). REQUIRED."
            },
            "payment_method_id": {
                "type": "string",
                "description": "Payment method identifier. REQUIRED."
            },
            "address_id": {
                "type": "string",
                "description": "Shipping address identifier. REQUIRED."
            }
        },
        "required": ["cart_id", "payment_method_id", "address_id"]
    },
    "returns": {
        "type": "object",
        "description": "Order confirmation or constraint (OUT_OF_STOCK, CART_NOT_FOUND)"
    }
}

# Registry of all tools
ALL_TOOLS = [
    TOOL_SEARCH_PRODUCTS,
    TOOL_GET_PRODUCT,
    TOOL_ADD_TO_CART,
    TOOL_CHECKOUT
]


def get_tools_by_category(category: ToolCategory) -> List[Dict[str, Any]]:
    """Get all tools in a specific category."""
    return [tool for tool in ALL_TOOLS if tool["category"] == category]


def get_tool_by_name(name: str) -> Dict[str, Any]:
    """Get a specific tool by name."""
    for tool in ALL_TOOLS:
        if tool["name"] == name:
            return tool
    raise ValueError(f"Tool '{name}' not found")


# 
# Provider-Specific Adapters
# 

def to_openai_function(tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert canonical tool to OpenAI function calling format.
    
    OpenAI format:
    {
        "name": "...",
        "description": "...",
        "parameters": {...}  # JSON Schema
    }
    """
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["parameters"]
    }


def to_gemini_function(tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert canonical tool to Google Gemini function declaration format.
    
    Gemini format (similar to OpenAI but with slight differences):
    {
        "name": "...",
        "description": "...",
        "parameters": {...}  # JSON Schema with type
    }
    """
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["parameters"]
    }


def to_claude_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert canonical tool to Claude tool use format.
    
    Claude format:
    {
        "name": "...",
        "description": "...",
        "input_schema": {...}  # JSON Schema
    }
    """
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["parameters"]
    }


def get_all_tools_for_provider(provider: str) -> List[Dict[str, Any]]:
    """
    Get all tools formatted for a specific provider.
    
    Args:
        provider: One of "openai", "gemini", "claude"
    
    Returns:
        List of tools in provider-specific format
    """
    if provider == "openai":
        return [to_openai_function(tool) for tool in ALL_TOOLS]
    elif provider == "gemini":
        return [to_gemini_function(tool) for tool in ALL_TOOLS]
    elif provider == "claude":
        return [to_claude_tool(tool) for tool in ALL_TOOLS]
    else:
        raise ValueError(f"Unknown provider: {provider}")
