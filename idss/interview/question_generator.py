"""
LLM-based question generator for the interview phase.

Generates contextual clarifying questions based on what's already known
about the user's preferences.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import json

from openai import OpenAI

from idss.utils.logger import get_logger
from idss.core.config import get_config
from idss.interview.preference_slots import get_slot_status, format_slot_context

logger = get_logger("interview.question_generator")


class QuestionResponse(BaseModel):
    """Structured output for question generation."""
    question: str = Field(
        description="The clarifying question to ask the user (1-2 sentences)"
    )
    quick_replies: List[str] = Field(
        default_factory=list,
        description="2-4 short answer options the user can click (2-5 words each)"
    )
    topic: str = Field(
        description="The topic this question addresses (e.g., 'budget', 'usage', 'features')"
    )


def get_system_prompt_template(domain: str = "vehicles") -> str:
    """Get domain-specific system prompt template."""
    
    domain_names = {
        "vehicles": "car shopping",
        "laptops": "laptop shopping", 
        "books": "book shopping"
    }
    domain_name = domain_names.get(domain, "shopping")
    
    return f"""You are a helpful {domain_name} assistant gathering preferences to make great recommendations.

## Current Knowledge
{{slot_context}}

## CRITICAL RULE
Your question MUST end with an invitation to share the topics listed in "Invite input on". This is required, not optional.

## Question Format
1. Main question about the FIRST item in the highest priority group
2. Quick replies (3-4 options) for that topic only
3. ALWAYS end with: "Feel free to also share [topics from 'Invite input on']"

## Examples

Example 1 - HIGH priority:
Context: "Invite input on: Primary Use, Body Style"
Question: "What's your budget range? Feel free to also share what you'll primarily use the vehicle for or what body style you prefer."
Quick replies: ["Under $20k", "$20k-$35k", "$35k-$50k", "Over $50k"]

Example 2 - MEDIUM priority:
Context: "Invite input on: Brand"
Question: "What key features matter most to you? Feel free to also share any brand preferences."
Quick replies: ["Fuel efficiency", "Safety features", "Cargo space", "Tech/entertainment"]

Example 3 - LOW priority:
Context: "Invite input on: New vs Used"
Question: "Do you have a fuel type preference? Feel free to also mention if you prefer new or used."
Quick replies: ["No preference", "Hybrid/Electric", "Gasoline only", "Diesel"]

Generate ONE question. Remember: ALWAYS include the invitation at the end."""



def generate_question(
    conversation_history: List[Dict[str, str]],
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any],
    questions_asked: List[str],
    domain: str = "vehicles"
) -> QuestionResponse:
    """
    Generate the next clarifying question based on context.

    Args:
        conversation_history: Previous messages
        explicit_filters: Current known filters
        implicit_preferences: Current known preferences
        questions_asked: List of topics already asked about

    Returns:
        QuestionResponse with question, quick_replies, and topic
    """
    config = get_config()
    client = OpenAI()

    # Get slot status and format for LLM context
    slot_status = get_slot_status(explicit_filters or {}, implicit_preferences or {})
    slot_context = format_slot_context(slot_status)

    logger.debug(f"Slot context for question generation:\n{slot_context}")

    # Build domain-aware system prompt with slot context
    system_prompt_template = get_system_prompt_template(domain)
    system_prompt = system_prompt_template.format(slot_context=slot_context)

    # Build messages
    messages = [
        {"role": "system", "content": system_prompt}
    ]

    # Add conversation history
    for msg in conversation_history[-4:]:  # Last 4 messages
        messages.append(msg)

    # Add instruction
    messages.append({
        "role": "user",
        "content": "Generate the next clarifying question to ask the user."
    })

    try:
        response = client.beta.chat.completions.parse(
            model=config.question_generator_model,
            messages=messages,
            response_format=QuestionResponse,
            temperature=0.7  # Slightly higher for variety
        )

        result = response.choices[0].message.parsed
        logger.info(f"Generated question: {result.question}")
        logger.info(f"Quick replies: {result.quick_replies}")
        logger.info(f"Topic: {result.topic}")

        return result

    except Exception as e:
        logger.error(f"Failed to generate question: {e}")
        # Return a default question on error
        return QuestionResponse(
            question="What are you looking for in your next vehicle?",
            quick_replies=["Daily commuter", "Family car", "Weekend fun", "Work vehicle"],
            topic="use_case"
        )


DIMENSION_QUESTION_PROMPT = """You are a helpful car shopping assistant. Generate a natural clarifying question
about the specific dimension mentioned below.

## Dimension to ask about: {dimension}
## Topic name: {topic}

## Distribution in current inventory:
{distribution_info}

## Guidelines
1. Ask a natural, conversational question about this specific aspect
2. Frame the question based on the inventory distribution shown above
3. Give 2-4 clickable quick reply options that reflect the actual values available
4. Keep the question brief (1-2 sentences)

## Current Context
{context}

Generate a question that helps narrow down the user's preference for {topic}."""


def generate_question_for_dimension(
    dimension: str,
    dimension_context: Dict[str, Any],
    conversation_history: List[Dict[str, str]],
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any],
) -> QuestionResponse:
    """
    Generate a question focused on a specific dimension.

    This is used by the entropy-based question selector to phrase questions
    about high-entropy dimensions naturally.

    Args:
        dimension: The dimension to ask about (e.g., 'price', 'body_style')
        dimension_context: Context about the dimension's distribution
        conversation_history: Previous messages
        explicit_filters: Current known filters
        implicit_preferences: Current known preferences

    Returns:
        QuestionResponse with question, quick_replies, and topic
    """
    config = get_config()
    client = OpenAI()

    topic = dimension_context.get("topic", dimension)

    # Build distribution info
    if dimension_context.get("is_numerical"):
        distribution_info = f"Range: {dimension_context.get('range_display', 'varies')}"
    elif dimension_context.get("top_values"):
        distribution_info = f"Most common options: {', '.join(dimension_context['top_values'][:5])}"
    else:
        distribution_info = "Various options available"

    # Build context
    context_parts = []
    if explicit_filters:
        filters_clean = {k: v for k, v in explicit_filters.items() if v is not None}
        if filters_clean:
            context_parts.append(f"Known filters: {json.dumps(filters_clean)}")
    if implicit_preferences:
        prefs_clean = {k: v for k, v in implicit_preferences.items() if v}
        if prefs_clean:
            context_parts.append(f"Known preferences: {json.dumps(prefs_clean)}")

    context = "\n".join(context_parts) if context_parts else "No information gathered yet."

    prompt = DIMENSION_QUESTION_PROMPT.format(
        dimension=dimension,
        topic=topic,
        distribution_info=distribution_info,
        context=context
    )

    messages = [{"role": "system", "content": prompt}]

    # Add recent conversation history
    for msg in conversation_history[-2:]:
        messages.append(msg)

    messages.append({
        "role": "user",
        "content": f"Generate a clarifying question about {topic}."
    })

    try:
        response = client.beta.chat.completions.parse(
            model=config.question_generator_model,
            messages=messages,
            response_format=QuestionResponse,
            temperature=0.7
        )

        result = response.choices[0].message.parsed
        # Override topic with the dimension we're asking about
        result.topic = dimension

        logger.info(f"Generated dimension question for '{dimension}': {result.question}")
        return result

    except Exception as e:
        logger.error(f"Failed to generate dimension question: {e}")
        # Return a default question for this dimension
        return _get_default_dimension_question(dimension, topic)


def _get_default_dimension_question(dimension: str, topic: str) -> QuestionResponse:
    """Get a default question for a dimension if LLM fails."""
    defaults = {
        "price": QuestionResponse(
            question="What's your budget range for this vehicle?",
            quick_replies=["Under $25k", "$25k-$40k", "$40k-$60k", "Over $60k"],
            topic="price"
        ),
        "body_style": QuestionResponse(
            question="What type of vehicle are you looking for?",
            quick_replies=["SUV", "Sedan", "Truck", "Hatchback"],
            topic="body_style"
        ),
        "fuel_type": QuestionResponse(
            question="Do you have a preference for fuel type?",
            quick_replies=["Gasoline", "Hybrid", "Electric", "No preference"],
            topic="fuel_type"
        ),
        "drivetrain": QuestionResponse(
            question="What drivetrain do you prefer?",
            quick_replies=["AWD/4WD", "Front-wheel", "Rear-wheel", "No preference"],
            topic="drivetrain"
        ),
        "make": QuestionResponse(
            question="Are there any brands you're particularly interested in?",
            quick_replies=["Toyota", "Honda", "Ford", "Open to anything"],
            topic="make"
        ),
    }
    return defaults.get(dimension, QuestionResponse(
        question=f"What's your preference for {topic}?",
        quick_replies=["No preference", "Tell me more"],
        topic=dimension
    ))


def generate_recommendation_intro(
    explicit_filters: Dict[str, Any],
    implicit_preferences: Dict[str, Any],
    diversification_dimension: str,
    bucket_labels: List[str]
) -> str:
    """
    Generate an introduction message for the recommendations.

    Args:
        explicit_filters: Known filters
        implicit_preferences: Known preferences
        diversification_dimension: The dimension used for diversification
        bucket_labels: Labels for each bucket/row

    Returns:
        Introduction message string
    """
    config = get_config()
    client = OpenAI()

    # Build context
    filters_clean = {k: v for k, v in explicit_filters.items() if v is not None}
    prefs_clean = {k: v for k, v in implicit_preferences.items() if v}

    prompt = f"""Generate a brief (2-3 sentences) introduction for vehicle recommendations.

**User's criteria:**
{json.dumps(filters_clean, indent=2) if filters_clean else "Not specified"}

**User's preferences:**
{json.dumps(prefs_clean, indent=2) if prefs_clean else "Not specified"}

**How results are organized:**
The results are diversified by {diversification_dimension}, showing options across:
{', '.join(bucket_labels)}

Write a friendly, helpful intro that:
1. Acknowledges what the user is looking for
2. Briefly explains how the results are organized
3. Invites them to explore the options

Keep it natural and conversational. Don't use bullet points."""

    try:
        response = client.chat.completions.create(
            model=config.semantic_parser_model,  # Use faster model for this
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=200
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Failed to generate intro: {e}")
        return f"Based on your preferences, I've found some great options organized by {diversification_dimension}. Take a look!"
