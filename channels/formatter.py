"""
formatter.py — converts ChatResponse objects to channel-specific message formats.

Currently supports:
  - Slack Block Kit (format_for_slack)

Each function returns a dict/list that can be passed directly to the channel SDK.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Markdown → Slack mrkdwn helpers
# ---------------------------------------------------------------------------

def _md_to_mrkdwn(text: str) -> str:
    """Convert common markdown to Slack mrkdwn syntax."""
    # Strip HTML tags first (before converting links so Slack <url|text> isn't stripped)
    text = re.sub(r"<[^>]+>", "", text)
    # [text](url) → <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    # **bold** → *bold*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # __bold__ → *bold*
    text = re.sub(r"__(.+?)__", r"*\1*", text)
    return text


# ---------------------------------------------------------------------------
# Slack Block Kit formatter
# ---------------------------------------------------------------------------

_MAX_SECTION_TEXT = 3000  # Slack limit per section block text


def _text_block(text: str) -> Dict[str, Any]:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text[:_MAX_SECTION_TEXT]},
    }


def _header_block(text: str) -> Dict[str, Any]:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": text[:150], "emoji": True},
    }


def _divider() -> Dict[str, Any]:
    return {"type": "divider"}


def _product_block(product: Dict[str, Any]) -> Dict[str, Any]:
    """Format a single product dict as a Slack section block."""
    name = product.get("name") or product.get("title") or "Product"
    price = product.get("price_value") or product.get("price")
    link = product.get("link") or product.get("merchant_product_url") or product.get("url")
    brand = product.get("brand", "")
    rating = product.get("rating")

    lines: List[str] = []
    if brand:
        lines.append(f"*{brand}* — {name}" if brand else name)
    else:
        lines.append(f"*{name}*")

    if price is not None:
        try:
            lines.append(f"Price: *${float(price):,.2f}*")
        except (TypeError, ValueError):
            lines.append(f"Price: {price}")

    if rating:
        lines.append(f"Rating: {rating}/5")

    if link:
        lines.append(f"<{link}|View product>")

    block: Dict[str, Any] = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)[:_MAX_SECTION_TEXT]},
    }

    image = product.get("image_url") or product.get("image")
    if image:
        block["accessory"] = {
            "type": "image",
            "image_url": image,
            "alt_text": name[:2000],
        }

    return block


def format_for_slack(response: Any) -> List[Dict[str, Any]]:
    """
    Convert a ChatResponse to a list of Slack Block Kit blocks.

    Accepts either a ChatResponse object or a plain dict.
    """
    # Support both object and dict
    def _get(attr: str, default: Any = None) -> Any:
        if isinstance(response, dict):
            return response.get(attr, default)
        return getattr(response, attr, default)

    response_type: str = _get("response_type", "question")
    message: str = _md_to_mrkdwn(_get("message", ""))
    recommendations: Optional[List[List[Dict]]] = _get("recommendations")
    bucket_labels: Optional[List[str]] = _get("bucket_labels")
    quick_replies: Optional[List[str]] = _get("quick_replies")
    comparison_data: Optional[Dict] = _get("comparison_data")
    research_data: Optional[Dict] = _get("research_data")

    blocks: List[Dict[str, Any]] = []

    if response_type == "recommendations" and recommendations:
        blocks.append(_header_block("Here are my recommendations:"))
        blocks.append(_divider())

        for row_idx, row in enumerate(recommendations):
            if bucket_labels and row_idx < len(bucket_labels):
                blocks.append(_text_block(f"*{bucket_labels[row_idx]}*"))
            for product in row:
                blocks.append(_product_block(product))
            if row_idx < len(recommendations) - 1:
                blocks.append(_divider())

        if message:
            blocks.append(_divider())
            blocks.append(_text_block(message))

    elif response_type in ("compare", "research"):
        if message:
            blocks.append(_text_block(message))

        # Flatten comparison attributes into readable text if present
        if comparison_data and isinstance(comparison_data, dict):
            attrs = comparison_data.get("attributes", [])
            products = comparison_data.get("products", [])
            if attrs and products:
                lines = []
                for attr in attrs[:10]:  # cap at 10 attributes
                    row = [f"*{attr}*"]
                    for p in products:
                        vals = p.get("values", {})
                        row.append(str(vals.get(attr, "—")))
                    lines.append(" | ".join(row))
                if lines:
                    blocks.append(_divider())
                    blocks.append(_text_block("\n".join(lines)))

    else:
        # Default: question or plain message
        if message:
            blocks.append(_text_block(message))

        if quick_replies:
            options_text = "  ".join(f"`{r}`" for r in quick_replies[:5])
            blocks.append(_text_block(f"Options: {options_text}"))

    # Fallback: never return empty blocks
    if not blocks:
        blocks.append(_text_block(message or "(no response)"))

    return blocks
