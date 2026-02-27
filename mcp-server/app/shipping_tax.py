"""
US 50-state (+ DC) sales tax rates and shipping cost calculator.

Tax rates are approximate base state rates for 2025 (no local add-ons).
Source: Tax Foundation / state revenue departments.

Shipping tiers:
  standard   — free (0¢)
  express    — $5.99 (599¢)
  overnight  — $14.99 (1499¢)

Per-product weight surcharge:
  0–5 lbs    → base rate
  5–20 lbs   → +$2.00 (200¢)
  20+ lbs    → +$5.00 (500¢)

Items with price > $2000 get +$3.00 oversized surcharge.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List


# ─── 50-State + DC tax rates ─────────────────────────────────────────────────
# Format: { "STATE_CODE": (rate_pct, display_name) }
# Rate is the combined state + average local rate (Tax Foundation 2025 est.)

STATE_TAX: Dict[str, tuple[float, str]] = {
    "AL": (9.24, "Alabama"),
    "AK": (1.76, "Alaska"),          # No state tax; avg local only
    "AZ": (8.37, "Arizona"),
    "AR": (9.47, "Arkansas"),
    "CA": (8.75, "California"),
    "CO": (7.77, "Colorado"),
    "CT": (6.35, "Connecticut"),
    "DE": (0.00, "Delaware"),         # No sales tax
    "FL": (7.01, "Florida"),
    "GA": (7.37, "Georgia"),
    "HI": (4.44, "Hawaii"),
    "ID": (6.02, "Idaho"),
    "IL": (8.85, "Illinois"),
    "IN": (7.00, "Indiana"),
    "IA": (6.94, "Iowa"),
    "KS": (8.68, "Kansas"),
    "KY": (6.00, "Kentucky"),
    "LA": (9.56, "Louisiana"),
    "ME": (5.50, "Maine"),
    "MD": (6.00, "Maryland"),
    "MA": (6.25, "Massachusetts"),
    "MI": (6.00, "Michigan"),
    "MN": (7.46, "Minnesota"),
    "MS": (7.07, "Mississippi"),
    "MO": (8.28, "Missouri"),
    "MT": (0.00, "Montana"),          # No sales tax
    "NE": (6.94, "Nebraska"),
    "NV": (8.23, "Nevada"),
    "NH": (0.00, "New Hampshire"),    # No sales tax
    "NJ": (6.60, "New Jersey"),
    "NM": (7.84, "New Mexico"),
    "NY": (8.52, "New York"),
    "NC": (6.99, "North Carolina"),
    "ND": (6.96, "North Dakota"),
    "OH": (7.22, "Ohio"),
    "OK": (8.95, "Oklahoma"),
    "OR": (0.00, "Oregon"),           # No sales tax
    "PA": (6.34, "Pennsylvania"),
    "RI": (7.00, "Rhode Island"),
    "SC": (7.44, "South Carolina"),
    "SD": (6.40, "South Dakota"),
    "TN": (9.55, "Tennessee"),
    "TX": (8.19, "Texas"),
    "UT": (7.19, "Utah"),
    "VT": (6.24, "Vermont"),
    "VA": (5.65, "Virginia"),
    "WA": (9.38, "Washington"),
    "WV": (6.52, "West Virginia"),
    "WI": (5.42, "Wisconsin"),
    "WY": (5.44, "Wyoming"),
    "DC": (6.00, "Washington D.C."),
}

# Ordered list for UI dropdowns
ALL_STATES: List[dict] = sorted(
    [{"code": code, "name": name, "tax_rate": rate}
     for code, (rate, name) in STATE_TAX.items()],
    key=lambda s: s["name"],
)

# Shipping base costs in cents
_SHIPPING_BASE: Dict[str, int] = {
    "standard": 0,
    "express": 599,
    "overnight": 1499,
}

# Weight-tier surcharges in cents (added on top of base)
_WEIGHT_SURCHARGE = {
    "light": 0,      # 0–5 lbs
    "medium": 200,   # 5–20 lbs
    "heavy": 500,    # 20+ lbs
}

_OVERSIZED_PRICE_THRESHOLD = 200_000  # $2000 in cents → oversized surcharge
_OVERSIZED_SURCHARGE = 300  # $3.00


# ─── Public API ──────────────────────────────────────────────────────────────

@dataclass
class LineItemTotals:
    product_id: str
    unit_price_cents: int
    quantity: int
    subtotal_cents: int


@dataclass
class ShippingTaxResult:
    state_code: str
    state_name: str
    tax_rate_pct: float
    shipping_method: str
    subtotal_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    line_items: list[LineItemTotals]


def get_tax_rate(state_code: str) -> tuple[float, str]:
    """Return (rate_pct, state_name) for a 2-letter state code (case-insensitive)."""
    code = state_code.upper().strip()
    if code not in STATE_TAX:
        raise ValueError(f"Unknown state code: {state_code!r}. Use a 2-letter US state code.")
    return STATE_TAX[code]


def _weight_tier(product_weight_lbs: float | None) -> str:
    if product_weight_lbs is None:
        return "light"
    if product_weight_lbs <= 5:
        return "light"
    if product_weight_lbs <= 20:
        return "medium"
    return "heavy"


def calculate_shipping(
    state_code: str,
    shipping_method: str,
    items: list[dict],
) -> ShippingTaxResult:
    """
    Calculate full shipping + tax breakdown.

    Args:
        state_code: 2-letter US state code (e.g. "CA")
        shipping_method: "standard" | "express" | "overnight"
        items: list of dicts, each must have:
            - product_id: str
            - unit_price_cents: int  (or unit_price_dollars: float)
            - quantity: int
            Optional:
            - weight_lbs: float   (for weight surcharge)

    Returns:
        ShippingTaxResult with full breakdown
    """
    method = shipping_method.lower()
    if method not in _SHIPPING_BASE:
        raise ValueError(f"Unknown shipping method: {method!r}")

    tax_rate_pct, state_name = get_tax_rate(state_code)

    line_totals: list[LineItemTotals] = []
    subtotal_cents = 0

    for item in items:
        # Accept either cents or dollars
        if "unit_price_cents" in item:
            unit_cents = int(item["unit_price_cents"])
        elif "unit_price_dollars" in item:
            unit_cents = round(float(item["unit_price_dollars"]) * 100)
        else:
            raise ValueError(f"Item missing unit_price_cents or unit_price_dollars: {item}")

        qty = int(item.get("quantity", 1))
        sub = unit_cents * qty
        subtotal_cents += sub
        line_totals.append(LineItemTotals(
            product_id=str(item.get("product_id", "")),
            unit_price_cents=unit_cents,
            quantity=qty,
            subtotal_cents=sub,
        ))

    # Shipping: base + weight surcharges + oversized
    base = _SHIPPING_BASE[method]
    surcharge = 0
    if method != "standard":
        for item in items:
            tier = _weight_tier(item.get("weight_lbs"))
            surcharge += _WEIGHT_SURCHARGE[tier] * item.get("quantity", 1)
    oversized_count = sum(
        lt.quantity for lt in line_totals
        if lt.unit_price_cents >= _OVERSIZED_PRICE_THRESHOLD
    )
    if oversized_count and method != "standard":
        surcharge += _OVERSIZED_SURCHARGE * oversized_count

    shipping_cents = base + surcharge

    # Tax on subtotal only (not shipping — most US states exempt shipping)
    tax_cents = round(subtotal_cents * tax_rate_pct / 100)
    total_cents = subtotal_cents + shipping_cents + tax_cents

    return ShippingTaxResult(
        state_code=state_code.upper(),
        state_name=state_name,
        tax_rate_pct=tax_rate_pct,
        shipping_method=method,
        subtotal_cents=subtotal_cents,
        shipping_cents=shipping_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
        line_items=line_totals,
    )
