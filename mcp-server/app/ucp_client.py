"""
UCP HTTP client: agent → MCP over HTTP.

The agent (e.g. action endpoints) builds UCP requests and sends them to the MCP
server over HTTP. MCP_BASE_URL points to the MCP service (default http://localhost:8001
when agent and MCP run in the same deployment).
"""

import logging
import os

import httpx
from pydantic import BaseModel

from app.ucp_schemas import (
    UCPGetCartRequest,
    UCPGetCartResponse,
    UCPAddToCartRequest,
    UCPAddToCartResponse,
    UCPRemoveFromCartRequest,
    UCPRemoveFromCartResponse,
    UCPCheckoutRequest,
    UCPCheckoutResponse,
    UCPUpdateCartRequest,
    UCPUpdateCartResponse,
)

logger = logging.getLogger("mcp.ucp_client")

# Base URL of the MCP server (UCP endpoints). When agent and MCP are separate, set MCP_BASE_URL.
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:8001").rstrip("/")

# Timeout for UCP HTTP calls
UCP_REQUEST_TIMEOUT = float(os.environ.get("UCP_REQUEST_TIMEOUT", "30.0"))


async def _post_ucp_async(
    path: str,
    body: BaseModel,
    response_model: type[BaseModel],
) -> BaseModel:
    """POST UCP request to MCP server over HTTP; return parsed UCP response."""
    url = f"{MCP_BASE_URL}{path}"
    payload = body.model_dump(mode="json")
    try:
        async with httpx.AsyncClient(timeout=UCP_REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                # MCP may return 4xx/5xx with UCP error body
                try:
                    data = e.response.json()
                    return response_model.model_validate(data)
                except Exception:
                    logger.warning("ucp_client: %s HTTP %s body=%s", path, e.response.status_code, e.response.text[:500])
                    raise
            data = resp.json()
            return response_model.model_validate(data)
    except httpx.RequestError as e:
        logger.error("ucp_client: %s request failed: %s", path, e)
        # Return an error response so the agent can return a structured failure
        if response_model == UCPGetCartResponse:
            return UCPGetCartResponse(status="error", error=f"MCP unreachable: {e}", items=[], item_count=0)
        if response_model == UCPAddToCartResponse:
            return UCPAddToCartResponse(status="error", error=f"MCP unreachable: {e}")
        if response_model == UCPRemoveFromCartResponse:
            return UCPRemoveFromCartResponse(status="error", error=f"MCP unreachable: {e}")
        if response_model == UCPCheckoutResponse:
            return UCPCheckoutResponse(status="error", error=f"MCP unreachable: {e}")
        if response_model == UCPUpdateCartResponse:
            return UCPUpdateCartResponse(status="error", error=f"MCP unreachable: {e}")
        raise


async def ucp_client_get_cart(request: UCPGetCartRequest) -> UCPGetCartResponse:
    """Send UCP get_cart to MCP over HTTP."""
    return await _post_ucp_async("/ucp/get_cart", request, UCPGetCartResponse)


async def ucp_client_add_to_cart(request: UCPAddToCartRequest) -> UCPAddToCartResponse:
    """Send UCP add_to_cart to MCP over HTTP."""
    return await _post_ucp_async("/ucp/add_to_cart", request, UCPAddToCartResponse)


async def ucp_client_remove_from_cart(request: UCPRemoveFromCartRequest) -> UCPRemoveFromCartResponse:
    """Send UCP remove_from_cart to MCP over HTTP."""
    return await _post_ucp_async("/ucp/remove_from_cart", request, UCPRemoveFromCartResponse)


async def ucp_client_checkout(request: UCPCheckoutRequest) -> UCPCheckoutResponse:
    """Send UCP checkout to MCP over HTTP."""
    return await _post_ucp_async("/ucp/checkout", request, UCPCheckoutResponse)


async def ucp_client_update_cart(request: UCPUpdateCartRequest) -> UCPUpdateCartResponse:
    """Send UCP update_cart to MCP over HTTP."""
    return await _post_ucp_async("/ucp/update_cart", request, UCPUpdateCartResponse)
