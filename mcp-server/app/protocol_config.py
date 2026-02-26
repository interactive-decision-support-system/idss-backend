"""
Protocol configuration for the shopping agent.

The shopping agent reads COMMERCE_PROTOCOL to decide which protocol format to use
for outbound calls to the merchant server. The merchant server accepts both UCP
and ACP regardless of this setting (routing on request path).

Values: "ucp" (default) | "acp"
"""

import os

COMMERCE_PROTOCOL: str = os.environ.get("COMMERCE_PROTOCOL", "ucp").lower()


def get_protocol() -> str:
    """Return the currently configured commerce protocol: 'ucp' or 'acp'."""
    return COMMERCE_PROTOCOL


def is_acp() -> bool:
    """True if the shopping agent should use ACP for outbound commerce calls."""
    return COMMERCE_PROTOCOL == "acp"


def is_ucp() -> bool:
    """True if the shopping agent should use UCP for outbound commerce calls (default)."""
    return COMMERCE_PROTOCOL != "acp"
