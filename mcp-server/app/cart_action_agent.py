"""
Cart action agent: maps API action requests to UCP payloads.

The "agent" is the component that turns the action API request (e.g. fetch-cart, add-to-cart)
into a UCP request that MCP will execute. For signed-in users, cart_id = user_id (UUID).
"""

from typing import Any, Dict

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
