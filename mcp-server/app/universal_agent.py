"""
Universal Agent for IDSS.

This agent acts as the central brain for the Unified Pipeline.
It replaces the fragmented logic across `chat_endpoint.py`, `query_specificity.py`,
and `idss_adapter.py` with a single, schema-driven loop powered by LLMs.

Responsibilities:
1. Intent Detection (LLM - gpt-4o-mini)
2. State Management (Tracking filters and gathered info)
3. Criteria Extraction (LLM - gpt-4o-mini) with impatience/intent detection
4. Question Generation (LLM - gpt-4o)
5. Handoff to Search

Based on IDSS interview principles:
- Priority-based slot filling (HIGH -> MEDIUM -> LOW)
- Impatience detection (user wants to skip questions)
- Question limit (k) to avoid over-interviewing
- Explicit recommendation request detection
"""
import logging
import json
import os
from typing import Dict, Any, List, Optional
from enum import Enum
from openai import OpenAI
from pydantic import BaseModel, Field

from .domain_registry import get_domain_schema, DomainSchema, SlotPriority, PreferenceSlot

logger = logging.getLogger("mcp.universal_agent")

# Models configuration
# "Basic" model for structural tasks (domain detection, criteria extraction)
MODEL_BASIC = "gpt-4o-mini"
# "Powerful" model for natural language generation (question generation)
MODEL_POWERFUL = "gpt-4o"

# Interview configuration
DEFAULT_MAX_QUESTIONS = 3  # Maximum questions before showing recommendations

class AgentState(Enum):
    INTENT_DETECTION = "intent_detection"
    INTERVIEW = "interview"
    SEARCH = "search"
    COMPLETE = "complete"

class SlotValue(BaseModel):
    """A single extracted criteria value."""
    slot_name: str = Field(description=" The name of the slot (e.g. 'budget', 'brand')")
    value: str = Field(description="The extracted value as a string")

class ExtractedCriteria(BaseModel):
    """Structure for LLM extraction output with IDSS interview signals."""
    criteria: List[SlotValue] = Field(description="List of extracted filter values")
    reasoning: str = Field(description="Brief reasoning for extraction")
    # IDSS interview signals
    is_impatient: bool = Field(default=False, description="User wants to skip questions (e.g., 'just show me results', 'I don't care')")
    wants_recommendations: bool = Field(default=False, description="User explicitly asks for recommendations (e.g., 'show me options', 'what do you recommend')")

class DomainClassification(BaseModel):
    """Structure for domain classification output."""
    domain: str = Field(description="One of: vehicles, laptops, books, unknown")
    confidence: float = Field(description="Confidence score 0-1")

class GeneratedQuestion(BaseModel):
    """Structure for question generation (IDSS style with invitation pattern)."""
    question: str = Field(description="The clarifying question ending with an invitation to share other preferences")
    quick_replies: List[str] = Field(description="2-4 short answer options for the MAIN topic only (2-5 words each)")
    topic: str = Field(description="The main topic this question addresses")

class UniversalAgent:
    def __init__(self, session_id: str, history: List[Dict[str, str]] = None, max_questions: int = DEFAULT_MAX_QUESTIONS):
        self.session_id = session_id
        self.history = history or []
        self.domain: Optional[str] = None
        self.filters: Dict[str, Any] = {}
        self.state = AgentState.INTENT_DETECTION

        # IDSS interview state
        self.question_count = 0
        self.max_questions = max_questions
        self.questions_asked: List[str] = []  # Slot names we've asked about

        # Initialize OpenAI client
        # Relies on OPENAI_API_KEY environment variable
        self.client = OpenAI()
        
    def process_message(self, message: str) -> Dict[str, Any]:
        """
        Main entry point for processing a user message.
        Returns a response dictionary (question, recommendations, etc.).
        """
        # 0. Update History
        self.history.append({"role": "user", "content": message})
        
        # 1. Intent/Domain Detection (if not locked)
        if not self.domain:
            self.domain = self._detect_domain_from_message(message)
            if not self.domain or self.domain == "unknown":
                # Still unknown, ask for clarification
                response = {
                    "response_type": "question",
                    "message": "I can help with Cars, Laptops, or Books. What are you looking for today?",
                    "quick_replies": ["Cars", "Laptops", "Books"],
                    "session_id": self.session_id
                }
                self.history.append({"role": "assistant", "content": response["message"]})
                # Reset domain so we try again next time
                self.domain = None 
                return response
        
        # 2. Extract Criteria (Schema-Driven) with IDSS signals
        schema = get_domain_schema(self.domain)
        if not schema:
            logger.error(f"No schema found for domain {self.domain}")
            return self._unknown_error_response()

        extraction_result = self._extract_criteria(message, schema)

        # 3. Check IDSS interview signals - should we skip to recommendations?
        if self._should_recommend(extraction_result, schema):
            logger.info(f"Skipping to recommendations (impatient={extraction_result.is_impatient if extraction_result else False}, "
                       f"wants_recs={extraction_result.wants_recommendations if extraction_result else False}, "
                       f"question_count={self.question_count}/{self.max_questions})")
            return self._handoff_to_search(schema)

        # 4. Check for Missing Information (Priority Check)
        missing_slot = self._get_next_missing_slot(schema)

        if missing_slot:
            # 5. Generate Question (LLM - gpt-4o)
            gen_q = self._generate_question(missing_slot, schema)

            # Track question asked
            self.questions_asked.append(missing_slot.name)
            self.question_count += 1

            response = {
                "response_type": "question",
                "message": gen_q.question,
                "quick_replies": gen_q.quick_replies,
                "session_id": self.session_id,
                "domain": self.domain,
                "filters": self.filters,
                "question_count": self.question_count
            }
            self.history.append({"role": "assistant", "content": response["message"]})
            return response

        else:
            # 6. Ready for Search - all slots filled or no more to ask
            return self._handoff_to_search(schema)

    def _detect_domain_from_message(self, message: str) -> Optional[str]:
        """
        Uses LLM (Basic Model) to classify intent.
        """
        try:
            logger.info(f"Detecting domain for message: {message[:50]}...")
            
            completion = self.client.beta.chat.completions.parse(
                model=MODEL_BASIC,
                messages=[
                    {"role": "system", "content": "You are a routing agent. Classify the user's intent into one of these domains: 'vehicles', 'laptops', 'books'. If unclear, return 'unknown'."},
                    {"role": "user", "content": message}
                ],
                response_format=DomainClassification,
            )
            result = completion.choices[0].message.parsed
            logger.info(f"Domain detected: {result.domain} (conf: {result.confidence})")
            
            if result.domain == "unknown":
                return None
            return result.domain
            
        except Exception as e:
            logger.error(f"Intent detection failed: {e}")
            return None

    def _extract_criteria(self, message: str, schema: DomainSchema) -> Optional[ExtractedCriteria]:
        """
        Uses LLM (gpt-4o-mini) to extract criteria based on the active schema.
        Also detects IDSS interview signals (impatience, recommendation requests).

        Input: User message + Schema Slots.
        Output: ExtractedCriteria with filters and signals.
        """
        try:
            # Construct a concise schema description for the LLM
            slots_desc = [f"- {s.name} ({s.description})" for s in schema.slots]
            schema_text = "\n".join(slots_desc)

            # Domain-specific context for realistic value interpretation
            domain_context = {
                "vehicles": """IMPORTANT: For vehicles, prices are typically in THOUSANDS of dollars.
- "under 20" or "20" means "$20,000" or "under $20k"
- "30-40" means "$30,000-$40,000" or "$30k-$40k"
- "50k" means "$50,000"
Always normalize budget values to include the "k" suffix (e.g., "$20k", "$30k-$40k", "under $25k").""",
                "laptops": """IMPORTANT: For laptops, prices are typically in HUNDREDS of dollars.
- "under 500" means "$500"
- "1000-2000" means "$1,000-$2,000"
Always include the dollar sign in budget values.""",
                "books": """IMPORTANT: For books, prices are typically under $50.
- "under 20" means "$20"
Always include the dollar sign in budget values."""
            }
            price_context = domain_context.get(schema.domain, "")

            system_prompt = f"""You are a smart extraction agent for the '{schema.domain}' domain.
Your goal is to extract specific criteria from the user's message based on the available slots:
{schema_text}

{price_context}

Also detect user intent signals:
- is_impatient: Set to true if user wants to skip questions or seems eager to see results.
  Examples: "just show me options", "I don't care about details", "whatever works", "skip"
- wants_recommendations: Set to true if user explicitly asks for recommendations.
  Examples: "show me what you have", "what do you recommend", "let's see some options"

Return a list of extracted criteria (slot names and values).
Only include slots that are explicitly mentioned or clearly inferred.
Do NOT guess. If a slot is not mentioned, do not include it.
"""

            logger.info(f"Extracting criteria for domain: {schema.domain}")

            completion = self.client.beta.chat.completions.parse(
                model=MODEL_BASIC,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                response_format=ExtractedCriteria,
            )
            result = completion.choices[0].message.parsed

            # Merge extracted filters into state
            if result.criteria:
                new_filters = {item.slot_name: item.value for item in result.criteria}
                logger.info(f"Extracted filters: {new_filters}")
                self.filters.update(new_filters)

            # Log IDSS signals
            if result.is_impatient:
                logger.info("User is impatient - will skip to recommendations")
            if result.wants_recommendations:
                logger.info("User explicitly wants recommendations")

            return result

        except Exception as e:
            logger.error(f"Criteria extraction failed: {e}")
            return None

    def _should_recommend(self, extraction_result: Optional[ExtractedCriteria], schema: DomainSchema) -> bool:
        """
        IDSS-style decision: Should we show recommendations now?

        Returns True ONLY if:
        - User is impatient (wants to skip questions)
        - User explicitly asks for recommendations
        - We've hit the question limit (max_questions)

        Does NOT stop early just because HIGH priority slots are filled.
        We continue asking MEDIUM priority questions until max_questions is reached.
        """
        # Check extraction signals
        if extraction_result:
            if extraction_result.is_impatient:
                logger.info("Recommend reason: User is impatient")
                return True
            if extraction_result.wants_recommendations:
                logger.info("Recommend reason: User requested recommendations")
                return True

        # Check question limit
        if self.question_count >= self.max_questions:
            logger.info(f"Recommend reason: Hit question limit ({self.max_questions})")
            return True

        # Don't stop early - let the interview continue with MEDIUM priority questions
        return False

    def _get_next_missing_slot(self, schema: DomainSchema) -> Optional[PreferenceSlot]:
        """
        Determines the next question to ask based on Priority.
        HIGH -> MEDIUM -> LOW (but respects questions already asked).
        """
        slots_by_priority = schema.get_slots_by_priority()

        # Check HIGH priority first
        for slot in slots_by_priority[SlotPriority.HIGH]:
            if slot.name not in self.filters and slot.name not in self.questions_asked:
                return slot

        # Check MEDIUM priority
        for slot in slots_by_priority[SlotPriority.MEDIUM]:
            if slot.name not in self.filters and slot.name not in self.questions_asked:
                return slot

        # LOW Priorities - strictly optional, skip for now
        # Could be enabled if we want more detailed interviews
        return None

    def _get_invite_topics(self, main_slot: PreferenceSlot, schema: DomainSchema) -> List[str]:
        """
        IDSS-style: Determine what other topics to invite input on.

        Logic:
        - If there are other slots at the same priority level, invite on those
        - If main slot is the last at its level, invite on next priority level
        """
        slots_by_priority = schema.get_slots_by_priority()

        # Get missing slots at each priority (excluding already filled/asked)
        def get_missing(slots: List[PreferenceSlot]) -> List[PreferenceSlot]:
            return [s for s in slots if s.name not in self.filters and s.name not in self.questions_asked]

        high = get_missing(slots_by_priority[SlotPriority.HIGH])
        medium = get_missing(slots_by_priority[SlotPriority.MEDIUM])
        low = get_missing(slots_by_priority[SlotPriority.LOW])

        # Determine invite topics based on IDSS logic
        if main_slot.priority == SlotPriority.HIGH:
            # Remove main slot from high list
            other_high = [s for s in high if s.name != main_slot.name]
            if other_high:
                return [s.display_name for s in other_high]
            elif medium:
                return [s.display_name for s in medium]
        elif main_slot.priority == SlotPriority.MEDIUM:
            other_medium = [s for s in medium if s.name != main_slot.name]
            if other_medium:
                return [s.display_name for s in other_medium]
            elif low:
                return [s.display_name for s in low]
        elif main_slot.priority == SlotPriority.LOW:
            other_low = [s for s in low if s.name != main_slot.name]
            if other_low:
                return [s.display_name for s in other_low]

        return []

    def _format_slot_context(self, main_slot: PreferenceSlot, schema: DomainSchema) -> str:
        """
        Format current state and invite topics for LLM context (IDSS style).
        """
        # What we know
        if self.filters:
            filled_str = "\n".join(f"- {k}: {v}" for k, v in self.filters.items())
        else:
            filled_str = "- Nothing yet"

        # What we're asking about
        main_topic = main_slot.display_name

        # What to invite input on
        invite_topics = self._get_invite_topics(main_slot, schema)
        if invite_topics:
            invite_str = f"Invite input on: {', '.join(invite_topics)}"
        else:
            invite_str = "No other topics to invite input on"

        return f"""**What we know:**
{filled_str}

**Main question topic:** {main_topic}

**{invite_str}**"""

    def _generate_question(self, slot: PreferenceSlot, schema: DomainSchema) -> GeneratedQuestion:
        """
        Uses LLM (gpt-4o) to generate a natural follow-up question.

        IDSS Style:
        1. Main question about the slot topic
        2. Quick replies for that topic only
        3. ALWAYS end with invitation to share other topics at same priority level
        """
        try:
            # Build IDSS-style context
            slot_context = self._format_slot_context(slot, schema)
            invite_topics = self._get_invite_topics(slot, schema)

            # Domain-specific assistant name
            domain_names = {
                "vehicles": "car shopping",
                "laptops": "laptop shopping",
                "books": "book recommendation"
            }
            assistant_type = domain_names.get(schema.domain, schema.domain)

            system_prompt = f"""You are a helpful {assistant_type} assistant gathering preferences to make great recommendations.

## Current Knowledge
{slot_context}

## CRITICAL RULE
Your question MUST end with an invitation to share the topics listed in "Invite input on". This is required, not optional.

## Question Format
1. Main question about '{slot.display_name}'
2. Quick replies (2-4 options) for that topic only
3. ALWAYS end with: "Feel free to also share [topics from 'Invite input on']"

## Examples

Example 1 (vehicles - budget with other HIGH topics):
Context: "Invite input on: Primary Use, Body Style"
Question: "What's your budget range? Feel free to also share what you'll primarily use the vehicle for or what body style you prefer."
Quick replies: ["Under $20k", "$20k-$35k", "$35k-$50k", "Over $50k"]

Example 2 (laptops - use case with other topics):
Context: "Invite input on: Budget, Brand"
Question: "What will you primarily use the laptop for? Feel free to also share your budget or any brand preferences."
Quick replies: ["Work/Business", "Gaming", "School/Study", "Creative Work"]

Example 3 (books - genre with other topics):
Context: "Invite input on: Format"
Question: "What genre of book are you in the mood for? Feel free to also mention if you prefer a specific format."
Quick replies: ["Fiction", "Mystery/Thriller", "Sci-Fi/Fantasy", "Non-Fiction"]

Generate ONE question. Topic: {slot.name}. Remember: ALWAYS include the invitation at the end."""

            completion = self.client.beta.chat.completions.parse(
                model=MODEL_POWERFUL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    # Include recent history for conversational flow context
                    *self.history[-3:]
                ],
                response_format=GeneratedQuestion,
                temperature=0.7  # Slightly higher for natural variety
            )
            result = completion.choices[0].message.parsed
            logger.info(f"Generated IDSS-style question: {result.question}")
            logger.info(f"Quick replies: {result.quick_replies}")
            logger.info(f"Topic: {result.topic}")
            return result

        except Exception as e:
            logger.error(f"Question generation failed: {e}")
            # Fallback to schema static data with basic invitation
            invite_topics = self._get_invite_topics(slot, schema)
            fallback_question = slot.example_question
            if invite_topics:
                fallback_question += f" Feel free to also share your preferences for {', '.join(invite_topics).lower()}."

            return GeneratedQuestion(
                question=fallback_question,
                quick_replies=slot.example_replies,
                topic=slot.name
            )

    def _handoff_to_search(self, schema: DomainSchema) -> Dict[str, Any]:
        """
        Constructs the search response/handoff with all gathered information.
        """
        response = {
            "response_type": "recommendations_ready",
            "message": "Let me find some great options for you...",
            "session_id": self.session_id,
            "domain": self.domain,
            "filters": self.filters,
            "schema_used": schema.domain,
            "question_count": self.question_count,
            "questions_asked": self.questions_asked
        }
        self.history.append({"role": "assistant", "content": response["message"]})
        logger.info(f"Handoff to search: domain={self.domain}, filters={self.filters}, questions_asked={self.question_count}")
        return response

    def _unknown_error_response(self):
        return {
            "response_type": "error",
            "message": "Something went wrong. Please try again.",
            "session_id": self.session_id
        }
