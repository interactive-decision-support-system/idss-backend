"""
Cart action agent: maps API action requests to UCP or ACP payloads.

The "agent" is the component that turns the action API request (e.g. fetch-cart, add-to-cart)
into a UCP or ACP request that MCP will execute.
For signed-in users, cart_id = user_id (UUID).

Protocol selection: read COMMERCE_PROTOCOL env var via protocol_config.
  "ucp" (default) → build_ucp_* functions
  "acp"           → build_acp_* functions
"""

from typing import Any, Dict, Optional

from app.ucp_schemas import (
    UCPGetCartRequest,
    UCPGetCartParameters,
    UCPAddToCartRequest,
    UCPAddToCartParameters,
    UCPRemoveFromCartRequest,
    UCPRemoveFromCartParameters,
    UCPCheckoutRequest,
    UCPCheckoutParameters,
    UCPUpdateCartRequest,
    UCPUpdateCartParameters,
)
from app.acp_schemas import (
    ACPCreateSessionRequest,
    ACPLineItemInput,
    ACPUpdateSessionRequest,
    ACPBuyer,
    ACPShippingAddress,
    ACPCompleteSessionRequest,
)


def build_ucp_get_cart(user_id: str) -> UCPGetCartRequest:
    """Build UCP get_cart request from fetch-cart action (user_id = cart_id)."""
    return UCPGetCartRequest(
        action="get_cart",
        parameters=UCPGetCartParameters(cart_id=user_id),
    )


def build_ucp_add_to_cart(
    user_id: str,
    product_id: str,
    quantity: int = 1,
    product_snapshot: Dict[str, Any] | None = None,
) -> UCPAddToCartRequest:
    """Build UCP add_to_cart request from add-to-cart action."""
    return UCPAddToCartRequest(
        action="add_to_cart",
        parameters=UCPAddToCartParameters(
            cart_id=user_id,
            product_id=product_id,
            quantity=quantity,
            product_snapshot=product_snapshot or {},
        ),
    )


def build_ucp_remove_from_cart(user_id: str, product_id: str) -> UCPRemoveFromCartRequest:
    """Build UCP remove_from_cart request from remove-from-cart action."""
    return UCPRemoveFromCartRequest(
        action="remove_from_cart",
        parameters=UCPRemoveFromCartParameters(cart_id=user_id, product_id=product_id),
    )


def build_ucp_checkout(user_id: str, shipping_method: str = "standard") -> UCPCheckoutRequest:
    """Build UCP checkout request from checkout action."""
    return UCPCheckoutRequest(
        action="checkout",
        parameters=UCPCheckoutParameters(cart_id=user_id, shipping_method=shipping_method),
    )


def build_ucp_update_cart(user_id: str, product_id: str, quantity: int) -> UCPUpdateCartRequest:
    """Build UCP update_cart request (set quantity; 0 = remove)."""
    return UCPUpdateCartRequest(
        action="update_cart",
        parameters=UCPUpdateCartParameters(cart_id=user_id, product_id=product_id, quantity=quantity),
    )


# ============================================================================
# ACP Request Builders (Agentic Commerce Protocol — agenticcommerce.dev)
# ============================================================================

def build_acp_create_session(
    product_id: str,
    title: str,
    price_dollars: float,
    quantity: int = 1,
    image_url: Optional[str] = None,
) -> ACPCreateSessionRequest:
    """
    Build ACP create-checkout-session request from an add-to-cart action.

    ACP uses a checkout session model rather than a persistent cart.
    Each add-to-cart action creates (or appends to) a checkout session.

    price_dollars is accepted in dollars and converted to cents (unit_amount)
    to match the ACP 2026-01-30 spec which uses cents for all monetary values.
    """
    return ACPCreateSessionRequest(
        line_items=[
            ACPLineItemInput(
                product_id=product_id,
                name=title,
                unit_amount=int(round(price_dollars * 100)),
                quantity=quantity,
                image_url=image_url,
            )
        ]
    )


def build_acp_update_session(
    shipping_method: str = "standard",
    buyer_email: Optional[str] = None,
    shipping_address: Optional[Dict[str, Any]] = None,
) -> ACPUpdateSessionRequest:
    """
    Build ACP update-checkout-session request.

    Adds buyer info, shipping address, and/or shipping method.
    """
    buyer = ACPBuyer(email=buyer_email) if buyer_email else None
    addr = None
    if shipping_address:
        addr = ACPShippingAddress(
            street=shipping_address.get("street"),
            city=shipping_address.get("city"),
            state=shipping_address.get("state"),
            postal_code=shipping_address.get("postal_code"),
            country=shipping_address.get("country", "US"),
        )
    return ACPUpdateSessionRequest(
        buyer=buyer,
        shipping_address=addr,
        shipping_method=shipping_method,
    )


def build_acp_complete_session(
    payment_token: Optional[str] = None,
    payment_method: str = "card",
) -> ACPCompleteSessionRequest:
    """Build ACP complete-checkout-session request (place order)."""
    return ACPCompleteSessionRequest(
        payment_token=payment_token,
        payment_method=payment_method,
    )
