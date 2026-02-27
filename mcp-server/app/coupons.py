"""
Coupon / discount code validation.

Demo coupon codes (suitable for the CS224N project demo):
  STANFORD10   — 10% off entire order
  SAVE20       — $20 off orders over $100
  FREESHIP     — free shipping (eliminates shipping fee)
  WELCOME5     — 5% off, no minimum
  TECH50       — $50 off electronics orders over $500
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class CouponResult:
    valid: bool
    code: str
    description: str
    discount_type: str        # "percent" | "fixed" | "free_shipping"
    discount_value: float     # pct (0–100) for percent; dollars for fixed
    discount_cents: int       # computed discount in cents (0 if invalid)
    error: Optional[str] = None


# ─── Coupon definitions ───────────────────────────────────────────────────────
# Each entry: (discount_type, value, min_order_cents, description)
_COUPONS: dict[str, tuple[str, float, int, str]] = {
    "STANFORD10":  ("percent",       10.0,      0, "10% off your entire order"),
    "SAVE20":      ("fixed",         20.0,  10000, "$20 off orders over $100"),
    "FREESHIP":    ("free_shipping",  0.0,      0, "Free shipping on this order"),
    "WELCOME5":    ("percent",        5.0,      0, "5% off — welcome discount"),
    "TECH50":      ("fixed",         50.0,  50000, "$50 off electronics over $500"),
}


def validate_coupon(code: str, subtotal_cents: int, shipping_cents: int) -> CouponResult:
    """
    Validate a coupon code and compute the discount amount.

    Args:
        code: The coupon code entered by the user (case-insensitive).
        subtotal_cents: Cart subtotal before shipping/tax.
        shipping_cents: Current shipping cost in cents.

    Returns:
        CouponResult with discount_cents set to the saving (>= 0).
    """
    normalized = code.strip().upper()

    if normalized not in _COUPONS:
        return CouponResult(
            valid=False,
            code=normalized,
            description="",
            discount_type="",
            discount_value=0.0,
            discount_cents=0,
            error="Invalid coupon code. Check spelling and try again.",
        )

    dtype, dvalue, min_order, description = _COUPONS[normalized]

    if subtotal_cents < min_order:
        min_dollars = min_order / 100
        return CouponResult(
            valid=False,
            code=normalized,
            description=description,
            discount_type=dtype,
            discount_value=dvalue,
            discount_cents=0,
            error=f"Order must be at least ${min_dollars:.2f} to use {normalized}.",
        )

    if dtype == "percent":
        discount_cents = round(subtotal_cents * dvalue / 100)
    elif dtype == "fixed":
        discount_cents = min(round(dvalue * 100), subtotal_cents)
    elif dtype == "free_shipping":
        discount_cents = shipping_cents  # eliminates shipping fee
    else:
        discount_cents = 0

    return CouponResult(
        valid=True,
        code=normalized,
        description=description,
        discount_type=dtype,
        discount_value=dvalue,
        discount_cents=discount_cents,
    )
