"""
LLM-based question generator for laptop/electronics interview phase.

Generates contextual clarifying questions based on what's already known
about the user's preferences, similar to the car IDSS system.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import json
import os

from openai import OpenAI

logger = None
try:
    from app.utils.logger import get_logger
    logger = get_logger("interview.question_generator")
except ImportError:
    import logging
    logger = logging.getLogger("interview.question_generator")


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
        description="The topic this question addresses (e.g., 'budget', 'use_case', 'brand', 'features')"
    )


SYSTEM_PROMPT_LAPTOP = """You are a helpful laptop shopping assistant. Your goal is to ask clarifying questions
to understand what the user is looking for so you can make great recommendations.

## Guidelines

1. **Be conversational**: Ask questions naturally, like a helpful salesperson would
2. **One question at a time**: Focus on one aspect per turn
3. **Build on context**: Use what you already know to ask relevant follow-ups
4. **Keep it brief**: 1-2 sentences for your question
5. **Provide quick replies**: Give 2-4 clickable answer options (2-5 words each)

## Question Progression
Ask about these topics in a natural order (skip what's already known):
1. **Use case**: What will they use the laptop for? (gaming, work, school, creative work, etc.)
2. **Budget**: What price range are they considering?
3. **Brand**: Any brand preferences? (Apple, Dell, HP, Lenovo, ASUS, etc.)
4. **Features**: What features matter most? (performance, battery life, portability, screen size, etc.)
5. **Other**: Any other preferences (color, size, specific specs, etc.)

## Important
- DON'T repeat questions about topics already covered
- DON'T ask about information already in the filters
- If a topic is in "Topics already asked about", NEVER ask about it again
- Check the conversation history to see what was already asked
- Make quick_replies diverse and helpful options
- If all important topics are covered, move to recommendations instead of asking again"""


SYSTEM_PROMPT_ELECTRONICS = """You are a helpful electronics shopping assistant. Your goal is to ask clarifying questions
to understand what the user is looking for so you can make great recommendations.

## Guidelines

1. **Be conversational**: Ask questions naturally, like a helpful salesperson would
2. **One question at a time**: Focus on one aspect per turn
3. **Build on context**: Use what you already know to ask relevant follow-ups
4. **Keep it brief**: 1-2 sentences for your question
5. **Provide quick replies**: Give 2-4 clickable answer options (2-5 words each)

## Question Progression
Ask about these topics in a natural order (skip what's already known):
1. **Use case**: What will they use it for? (work, entertainment, education, etc.)
2. **Budget**: What price range are they considering?
3. **Brand**: Any brand preferences?
4. **Features**: What features matter most? (performance, portability, specific specs, etc.)
5. **Other**: Any other preferences (color, size, etc.)

## Important
- DON'T repeat questions about topics already covered
- DON'T ask about information already in the filters
- If a topic is in "Topics already asked about", NEVER ask about it again
- Check the conversation history to see what was already asked
- Make quick_replies diverse and helpful options
- If all important topics are covered, move to recommendations instead of asking again"""


def generate_question(
    product_type: str,
    conversation_history: List[Dict[str, str]],
    explicit_filters: Dict[str, Any],
    questions_asked: List[str]
) -> QuestionResponse:
    """
    Generate the next clarifying question based on context.

    Args:
        product_type: Product type ("laptop", "electronics", etc.)
        conversation_history: Previous messages
        explicit_filters: Current known filters
        questions_asked: List of topics already asked about

    Returns:
        QuestionResponse with question, quick_replies, and topic
    """
    # Select appropriate system prompt
    if product_type == "laptop":
        system_prompt = SYSTEM_PROMPT_LAPTOP
    else:
        system_prompt = SYSTEM_PROMPT_ELECTRONICS
    
    # Get model from environment or use default
    model = os.getenv("QUESTION_GENERATOR_MODEL", "gpt-4o-mini")
    client = OpenAI()

    # Build context message
    context_parts = []

    if explicit_filters:
        filters_clean = {k: v for k, v in explicit_filters.items() if v is not None and not k.startswith("_")}
        if filters_clean:
            context_parts.append(f"**Known filters:**\n{json.dumps(filters_clean, indent=2)}")

    if questions_asked:
        context_parts.append(f"**Topics already asked about:** {', '.join(questions_asked)}")
        context_parts.append(f"**CRITICAL: DO NOT ask about these topics again:** {', '.join(questions_asked)}")

    context = "\n\n".join(context_parts) if context_parts else "No information gathered yet."

    # Build messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"## Current Context\n{context}"}
    ]

    # Add conversation history (last 4 messages)
    for msg in conversation_history[-4:]:
        messages.append(msg)

    # Add instruction
    messages.append({
        "role": "user",
        "content": "Generate the next clarifying question to ask the user."
    })

    try:
        response = client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=QuestionResponse,
            temperature=0.7  # Slightly higher for variety
        )

        result = response.choices[0].message.parsed
        if logger:
            logger.info(f"Generated question: {result.question}")
            logger.info(f"Quick replies: {result.quick_replies}")
            logger.info(f"Topic: {result.topic}")

        return result

    except Exception as e:
        if logger:
            logger.error(f"Failed to generate question: {e}")
        
        # Return a default question on error
        if product_type == "laptop":
            return QuestionResponse(
                question="What will you primarily use the laptop for?",
                quick_replies=["Gaming", "Work", "School", "Creative work"],
                topic="use_case"
            )
        else:
            return QuestionResponse(
                question="What will you use it for?",
                quick_replies=["Work", "Entertainment", "Education"],
                topic="use_case"
            )
