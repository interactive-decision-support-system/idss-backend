"""
LLM-based filter extractor for the interview flow.

Called when the user answers a follow-up question so the answer is parsed
in full context (what question was asked, what's already known) rather than
purely by regex on the raw text.

Example:
  Question: "Which brand do you prefer?"
  Answer:   "at least 500 gb"
  Regex:    extracts nothing (no storage keyword)
  LLM:      sees the mismatch, infers the user is answering about storage
            not brand, and extracts min_storage_gb=500.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("interview.filter_extractor")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "")
_REASONING_KWARGS = {"reasoning_effort": OPENAI_REASONING_EFFORT} if OPENAI_REASONING_EFFORT else {}


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class ExtractedFilters(BaseModel):
    """Structured filter values extracted from a conversational answer."""
    price_min: Optional[float] = Field(None, description="Minimum price in dollars")
    price_max: Optional[float] = Field(None, description="Maximum price in dollars")
    brand: Optional[str] = Field(None, description="Brand name, or null for no preference")
    min_ram_gb: Optional[int] = Field(None, description="Minimum RAM in GB")
    min_storage_gb: Optional[int] = Field(None, description="Minimum storage in GB")
    min_screen_inches: Optional[float] = Field(None, description="Minimum screen size in inches")
    min_battery_hours: Optional[int] = Field(None, description="Minimum battery life in hours")
    storage_type: Optional[str] = Field(None, description="'SSD' or 'HDD'")
    use_cases: Optional[List[str]] = Field(None, description="Use cases: gaming, ml, web_dev, programming, creative, linux")
    color: Optional[str] = Field(None, description="Preferred color")
    os: Optional[str] = Field(None, description="Operating system preference")


_SYSTEM_PROMPT = """\
You are a filter extraction assistant for a laptop/electronics shopping system.

The user is mid-conversation answering follow-up questions. Extract NEW filter \
values from their latest answer. You know:
- What question was just asked (so you can interpret the answer correctly)
- What filters are already known (so you don't duplicate them)
- The conversation history (so you have full context)

## Extraction rules

Prices
  "between $700 and $1500" → price_min=700, price_max=1500
  "under $1000" / "less than $1000" → price_max=1000
  "around $800" → price_min=700, price_max=900

Storage  (bare "N GB" or "N TB" counts as storage when the topic is storage)
  "500 GB", "at least 500gb", "500 gb storage" → min_storage_gb=500
  "1 TB", "1TB SSD" → min_storage_gb=1000
  "256 GB" → min_storage_gb=256

RAM
  "16 GB RAM", "at least 16GB memory", "16 gigs" → min_ram_gb=16

Screen
  "15 inch", "15-16 inch", "at least 15 inches" → min_screen_inches=15
  "14\"" → min_screen_inches=14

Brand
  Named brand (Apple, Dell, HP, Lenovo, ASUS, etc.) → brand="<name>"
  "no preference", "any", "don't care", "doesn't matter" → brand=null (omit)

Use cases
  "gaming" → use_cases=["gaming"]
  "machine learning / ML" → use_cases=["ml"]
  "web development" → use_cases=["web_dev"]
  "programming / coding" → use_cases=["programming"]
  "video editing / creative" → use_cases=["creative"]

Return ONLY filters that are NEW (not already in known_filters).
Return an empty object {} if nothing can be reliably extracted or the user \
expressed no preference.\
"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def extract_filters_from_answer(
    user_answer: str,
    conversation_history: List[Dict[str, str]],
    questions_asked: List[str],
    known_filters: Dict[str, Any],
    product_type: str = "laptop",
) -> Dict[str, Any]:
    """
    Use an LLM to extract structured search filters from a conversational answer.

    Only fires when there is at least one previous question in the session
    (i.e. we're in an ongoing interview, not handling the very first message).

    Returns a dict of filter key→value pairs to merge into the search filters,
    or {} if nothing new was extracted or the LLM call fails.
    """
    # Only run during an active interview (at least one question already asked)
    if not questions_asked or not conversation_history:
        return {}

    # Find the last question the assistant asked
    last_question = next(
        (m["content"] for m in reversed(conversation_history) if m.get("role") == "assistant"),
        None,
    )
    if not last_question:
        return {}

    last_topic = questions_asked[-1] if questions_asked else "unknown"

    # Build context for the LLM
    known_clean = {k: v for k, v in known_filters.items() if v is not None and not k.startswith("_")}

    user_prompt = (
        f"## Last question asked (topic: {last_topic}):\n{last_question}\n\n"
        f"## Already known filters:\n{known_clean}\n\n"
        f"## User's answer:\n{user_answer}\n\n"
        f"Extract only NEW filter values from this answer."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        response = await client.beta.chat.completions.parse(
            model=OPENAI_MODEL,
            **_REASONING_KWARGS,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ExtractedFilters,
        )

        extracted: ExtractedFilters = response.choices[0].message.parsed
        result: Dict[str, Any] = {}

        if extracted.price_min is not None:
            result["price_min"] = extracted.price_min
        if extracted.price_max is not None:
            result["price_max"] = extracted.price_max
        if extracted.brand is not None:
            result["brand"] = extracted.brand
        if extracted.min_ram_gb is not None:
            result["min_ram_gb"] = extracted.min_ram_gb
        if extracted.min_storage_gb is not None:
            result["min_storage_gb"] = extracted.min_storage_gb
        if extracted.min_screen_inches is not None:
            result["min_screen_inches"] = extracted.min_screen_inches
        if extracted.min_battery_hours is not None:
            result["min_battery_hours"] = extracted.min_battery_hours
        if extracted.storage_type is not None:
            result["storage_type"] = extracted.storage_type
        if extracted.use_cases:
            result["use_cases"] = extracted.use_cases
        if extracted.color is not None:
            result["color"] = extracted.color
        if extracted.os is not None:
            result["os"] = extracted.os

        if result:
            logger.info(f"LLM filter extraction (topic={last_topic}): {result}")
        return result

    except Exception as e:
        logger.warning(f"LLM filter extraction failed, falling back to regex: {e}")
        return {}
