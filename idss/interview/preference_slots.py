"""
Preference slot definitions for structured interview.

Defines vehicle preference slots with priorities to guide question generation.
The LLM uses this information to ask about important things first and
bundle related questions together.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SlotPriority(Enum):
    """Priority level for preference slots."""
    HIGH = 1      # Ask first (budget, use_case, body_style)
    MEDIUM = 2    # Ask if time (features)
    LOW = 3       # Ask if nothing else (brand, fuel_type, new_vs_used)


@dataclass
class PreferenceSlot:
    """Definition of a single preference slot."""
    name: str
    display_name: str
    priority: SlotPriority
    filter_key: Optional[str] = None       # Maps to explicit_filters
    preference_key: Optional[str] = None   # Maps to implicit_preferences
    example_question: str = ""
    example_replies: List[str] = field(default_factory=list)


# Predefined vehicle preference slots
VEHICLE_SLOTS = [
    PreferenceSlot(
        name="budget",
        display_name="Budget",
        priority=SlotPriority.HIGH,
        filter_key="price",
        example_question="What's your budget?",
        example_replies=["Under $20k", "$20k-$35k", "$35k-$50k", "Over $50k"]
    ),
    PreferenceSlot(
        name="use_case",
        display_name="Primary Use",
        priority=SlotPriority.HIGH,
        preference_key="use_case",
        example_question="What will you use this vehicle for?",
        example_replies=["Daily commute", "Family trips", "Off-road", "Work"]
    ),
    PreferenceSlot(
        name="body_style",
        display_name="Body Style",
        priority=SlotPriority.HIGH,
        filter_key="body_style",
        example_question="What type of vehicle?",
        example_replies=["SUV", "Sedan", "Truck", "Crossover"]
    ),
    PreferenceSlot(
        name="features",
        display_name="Key Features",
        priority=SlotPriority.MEDIUM,
        preference_key="liked_features",
        example_question="What features matter most?",
        example_replies=["Fuel efficiency", "Safety", "Tech", "Performance"]
    ),
    PreferenceSlot(
        name="brand",
        display_name="Brand",
        priority=SlotPriority.MEDIUM,
        filter_key="make",
        example_question="Any brand preference?",
        example_replies=["No preference", "Toyota/Honda", "Ford/Chevy", "BMW/Audi"]
    ),
    PreferenceSlot(
        name="fuel_type",
        display_name="Fuel Type",
        priority=SlotPriority.LOW,
        filter_key="fuel_type",
        example_question="Fuel preference?",
        example_replies=["No preference", "Hybrid/Electric", "Gas only"]
    ),
    PreferenceSlot(
        name="new_vs_used",
        display_name="New vs Used",
        priority=SlotPriority.LOW,
        filter_key="is_used",
        example_question="New or used?",
        example_replies=["New only", "Used only", "Either"]
    ),
]


def get_slot_status(explicit_filters: Dict[str, Any], implicit_preferences: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze current state and return slot status for LLM context.

    Args:
        explicit_filters: Current explicit filters (price, body_style, etc.)
        implicit_preferences: Current implicit preferences (liked_features, etc.)

    Returns:
        Dict with 'filled' and 'missing' slots, sorted by priority.
    """
    filled = {}
    missing = []

    for slot in sorted(VEHICLE_SLOTS, key=lambda s: s.priority.value):
        value = None

        # Check if slot has a value from filters
        if slot.filter_key and explicit_filters.get(slot.filter_key):
            value = explicit_filters[slot.filter_key]
        # Check if slot has a value from preferences
        elif slot.preference_key and implicit_preferences.get(slot.preference_key):
            value = implicit_preferences[slot.preference_key]

        if value:
            filled[slot.display_name] = value
        else:
            missing.append({
                "name": slot.name,
                "display_name": slot.display_name,
                "priority": slot.priority.name,
                "example_question": slot.example_question,
                "example_replies": slot.example_replies
            })

    return {"filled": filled, "missing": missing}


def format_slot_context(slot_status: Dict[str, Any]) -> str:
    """
    Format slot status into a string for LLM context.

    Args:
        slot_status: Result from get_slot_status()

    Returns:
        Formatted string showing what's known and what's missing.
    """
    # Format filled slots
    if slot_status["filled"]:
        filled_str = "\n".join(f"- {k}: {v}" for k, v in slot_status["filled"].items())
    else:
        filled_str = "- Nothing yet"

    # Format missing slots by priority
    if slot_status["missing"]:
        high = [s for s in slot_status["missing"] if s["priority"] == "HIGH"]
        medium = [s for s in slot_status["missing"] if s["priority"] == "MEDIUM"]
        low = [s for s in slot_status["missing"] if s["priority"] == "LOW"]

        missing_parts = []
        if high:
            missing_parts.append("HIGH PRIORITY (ask first): " + ", ".join(s["display_name"] for s in high))
        if medium:
            missing_parts.append("MEDIUM: " + ", ".join(s["display_name"] for s in medium))
        if low:
            missing_parts.append("LOW (only if time): " + ", ".join(s["display_name"] for s in low))
        missing_str = "\n".join(missing_parts)

        # Determine what to invite input on (highest priority remaining AFTER the main question)
        # Main question = first item in highest priority group
        # Invite = remaining items in same group, or next priority group
        if len(high) > 1:
            # Ask about high[0], invite on other HIGH items
            invite_topics = [s["display_name"] for s in high[1:]]
            invite_str = f"Invite input on: {', '.join(invite_topics)} (other HIGH priority)"
        elif high and medium:
            # Only 1 HIGH left (that's the main question), invite on MEDIUM
            invite_topics = [s["display_name"] for s in medium]
            invite_str = f"Invite input on: {', '.join(invite_topics)} (MEDIUM priority)"
        elif len(medium) > 1:
            # No HIGH left, ask about medium[0], invite on other MEDIUM items
            invite_topics = [s["display_name"] for s in medium[1:]]
            invite_str = f"Invite input on: {', '.join(invite_topics)} (other MEDIUM priority)"
        elif medium and low:
            # Only 1 MEDIUM left (that's the main question), invite on LOW
            invite_topics = [s["display_name"] for s in low]
            invite_str = f"Invite input on: {', '.join(invite_topics)} (LOW priority)"
        elif len(low) > 1:
            # No HIGH/MEDIUM left, ask about low[0], invite on other LOW items
            invite_topics = [s["display_name"] for s in low[1:]]
            invite_str = f"Invite input on: {', '.join(invite_topics)} (other LOW priority)"
        else:
            invite_str = "No other topics to invite input on"
    else:
        missing_str = "- All key info gathered!"
        invite_str = "No other topics to invite input on"

    return f"""**What we know:**
{filled_str}

**What we need:**
{missing_str}

**{invite_str}**"""
