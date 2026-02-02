"""
Semantic parser for extracting filters and detecting impatience.

Uses LLM with structured output to:
1. Extract explicit filters from user messages
2. Extract implicit preferences (liked/disliked features)
3. Detect if user is impatient and wants to skip to recommendations
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import json
import os

from openai import OpenAI

from idss.utils.logger import get_logger
from idss.core.config import get_config

logger = get_logger("parsing.semantic_parser")


class ExplicitFilters(BaseModel):
    """Explicit vehicle filters extracted from user input."""
    make: Optional[str] = Field(None, description="Vehicle make (e.g., 'Toyota', 'Honda,Ford')")
    model: Optional[str] = Field(None, description="Vehicle model (e.g., 'Camry', 'Civic,Accord')")
    year: Optional[str] = Field(None, description="Year or year range (e.g., '2020', '2018-2022')")
    price: Optional[str] = Field(None, description="Price range (e.g., '20000-30000')")
    mileage: Optional[str] = Field(None, description="Mileage range (e.g., '0-50000')")
    body_style: Optional[str] = Field(None, description="Body style (e.g., 'SUV', 'Sedan,Truck')")
    fuel_type: Optional[str] = Field(None, description="Fuel type (e.g., 'Electric', 'Hybrid,Gasoline')")
    drivetrain: Optional[str] = Field(None, description="Drivetrain (e.g., 'AWD', '4WD,FWD')")
    transmission: Optional[str] = Field(None, description="Transmission type (e.g., 'Automatic')")
    exterior_color: Optional[str] = Field(None, description="Exterior color preference")
    interior_color: Optional[str] = Field(None, description="Interior color preference")
    seating_capacity: Optional[int] = Field(None, description="Number of seats needed")
    is_used: Optional[bool] = Field(None, description="True for used, False for new")


class ImplicitPreferences(BaseModel):
    """Implicit preferences inferred from user input."""
    use_case: Optional[str] = Field(
        None,
        description="Primary use for the vehicle (e.g., 'daily commute', 'family trips', 'off-road adventures', 'city driving', 'road trips')"
    )
    liked_features: List[str] = Field(
        default_factory=list,
        description="Features the user likes (e.g., 'fuel efficiency', 'safety', 'spacious')"
    )
    disliked_features: List[str] = Field(
        default_factory=list,
        description="Features the user dislikes (e.g., 'poor visibility', 'high maintenance')"
    )


class ParsedInput(BaseModel):
    """Complete parsed output from user input."""
    explicit_filters: ExplicitFilters = Field(default_factory=ExplicitFilters)
    implicit_preferences: ImplicitPreferences = Field(default_factory=ImplicitPreferences)

    is_impatient: bool = Field(
        False,
        description="True if user seems impatient and wants to skip questions"
    )
    wants_recommendations: bool = Field(
        False,
        description="True if user explicitly asks for recommendations/results"
    )
    reasoning: str = Field(
        "",
        description="Brief explanation of the parsing decisions"
    )


SYSTEM_PROMPT = """You are a semantic parser for a vehicle recommendation system.

Your job is to analyze user messages and extract:
1. **Explicit filters**: Specific criteria like make, model, price, etc.
2. **Implicit preferences**: What features they like or dislike
3. **Impatience signals**: Whether they want to skip questions and see recommendations

## Guidelines

### Extracting Filters
- Only extract filters that are CLEARLY stated
- Use ranges for price/mileage/year (e.g., "under 30k" → "0-30000")
- Multiple values should be comma-separated (e.g., "Honda or Toyota" → "Honda,Toyota")
- Body styles: SUV, Sedan, Truck, Coupe, Hatchback, Convertible, Van, Wagon

### Brand Nationalities → Actual Makes
When users mention car nationalities, convert to actual brand names (only these brands are available):
- "German" → "BMW,Mercedes-Benz,Audi,Porsche,Volkswagen"
- "Japanese" → "Toyota,Honda,Nissan,Mazda,Subaru,Lexus,Acura,Infiniti,Mitsubishi"
- "American" → "Ford,Chevrolet,GMC,Dodge,Jeep,Ram,Cadillac,Lincoln,Buick,Chrysler,Tesla"
- "Korean" → "Hyundai,Kia,Genesis"
- "Italian" → "Alfa Romeo,Fiat,Maserati,Ferrari,Lamborghini"
- "Swedish" → "Volvo,Polestar"
- "British"/"English" → "Land Rover,Jaguar,Bentley,Rolls-Royce,Aston Martin,Lotus,McLaren,MINI"

NEVER set make to a nationality like "German" - always use actual brand names.
If a nationality has no available brands, leave make empty and explain in reasoning.

### Detecting Impatience
Set is_impatient=true if the user:
- Explicitly asks to skip questions or see results
- Gives very short, terse responses (like "yes", "no", "fine", "whatever")
- Expresses frustration with the questions
- Says things like "just show me", "enough questions", "let's see what you have"

Set wants_recommendations=true if the user explicitly asks to see vehicles/recommendations.

### Important
- Don't over-interpret: if something isn't clearly stated, don't infer it
- Be conservative with impatience detection - only flag clear signals
- Provide brief reasoning for your decisions"""


def parse_user_input(
    user_message: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    existing_filters: Optional[Dict[str, Any]] = None,
    question_count: int = 0
) -> ParsedInput:
    """
    Parse user input to extract filters, preferences, and impatience signals.

    Args:
        user_message: The user's latest message
        conversation_history: Previous messages [{"role": "user"|"assistant", "content": str}]
        existing_filters: Current filter state (for context)
        question_count: Number of questions asked so far (affects impatience sensitivity)

    Returns:
        ParsedInput with extracted information
    """
    config = get_config()
    client = OpenAI()

    # Build conversation context
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add context about current state
    context_parts = []
    if existing_filters:
        context_parts.append(f"Current filters: {json.dumps(existing_filters, indent=2)}")
    if question_count > 0:
        context_parts.append(f"Questions asked so far: {question_count}")

    if context_parts:
        messages.append({
            "role": "system",
            "content": "Context:\n" + "\n".join(context_parts)
        })

    # Add conversation history
    if conversation_history:
        for msg in conversation_history[-6:]:  # Last 6 messages for context
            messages.append(msg)

    # Add current message
    messages.append({
        "role": "user",
        "content": f"Parse this user message:\n\n\"{user_message}\""
    })

    try:
        response = client.beta.chat.completions.parse(
            model=config.semantic_parser_model,
            messages=messages,
            response_format=ParsedInput,
            temperature=config.temperature
        )

        parsed = response.choices[0].message.parsed
        logger.info(f"Parsed input: filters={parsed.explicit_filters.model_dump(exclude_none=True)}")
        logger.info(f"  preferences={parsed.implicit_preferences.model_dump()}")
        logger.info(f"  impatient={parsed.is_impatient}, wants_recs={parsed.wants_recommendations}")
        logger.info(f"  reasoning: {parsed.reasoning}")

        return parsed

    except Exception as e:
        logger.error(f"Failed to parse user input: {e}")
        # Return empty result on error
        return ParsedInput()


def merge_filters(
    existing: Dict[str, Any],
    new_filters: ExplicitFilters
) -> Dict[str, Any]:
    """
    Merge new filters into existing filters.

    New non-None values override existing values.

    Args:
        existing: Current filter dictionary
        new_filters: Newly extracted filters

    Returns:
        Merged filter dictionary
    """
    result = dict(existing)

    new_dict = new_filters.model_dump(exclude_none=True)
    for key, value in new_dict.items():
        if value is not None:
            result[key] = value

    return result


def merge_preferences(
    existing: Dict[str, Any],
    new_prefs: ImplicitPreferences
) -> Dict[str, Any]:
    """
    Merge new preferences into existing preferences.

    Appends to liked/disliked lists without duplicates.

    Args:
        existing: Current preferences dictionary
        new_prefs: Newly extracted preferences

    Returns:
        Merged preferences dictionary
    """
    result = {
        'use_case': existing.get('use_case'),
        'liked_features': list(existing.get('liked_features', [])),
        'disliked_features': list(existing.get('disliked_features', []))
    }

    # Update use_case if new one provided
    if new_prefs.use_case:
        result['use_case'] = new_prefs.use_case

    # Add new liked features
    for feature in new_prefs.liked_features:
        if feature not in result['liked_features']:
            result['liked_features'].append(feature)

    # Add new disliked features
    for feature in new_prefs.disliked_features:
        if feature not in result['disliked_features']:
            result['disliked_features'].append(feature)

    return result
