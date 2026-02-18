"""
Universal Agent for IDSS.

This agent acts as the central brain for the Unified Pipeline.
It replaces the fragmented logic across `chat_endpoint.py`, `query_specificity.py`,
and `idss_adapter.py` with a single, schema-driven loop powered by LLMs.

Responsibilities:
1. Intent Detection (LLM)
2. State Management (Tracking filters and gathered info)
3. Criteria Extraction (LLM) with impatience/intent detection
4. Question Generation (LLM)
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
import re
from typing import Dict, Any, List, Optional
from enum import Enum
from openai import OpenAI
from pydantic import BaseModel, Field

from .domain_registry import get_domain_schema, DomainSchema, SlotPriority, PreferenceSlot
from .prompts import (
    DOMAIN_DETECTION_PROMPT,
    CRITERIA_EXTRACTION_PROMPT,
    PRICE_CONTEXT,
    QUESTION_GENERATION_PROMPT,
    RECOMMENDATION_EXPLANATION_PROMPT,
    POST_REC_REFINEMENT_PROMPT,
    DOMAIN_ASSISTANT_NAMES,
)

logger = logging.getLogger("mcp.universal_agent")

# Model configuration — single model for all LLM calls, set via environment
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low")

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
    domain: str = Field(description="One of: vehicles, laptops, books, phones, unknown")
    confidence: float = Field(description="Confidence score 0-1")

class RefinementClassification(BaseModel):
    """Structure for post-recommendation refinement classification."""
    intent: str = Field(description="One of: refine_filters, domain_switch, new_search, action, other")
    new_domain: Optional[str] = Field(default=None, description="Target domain if domain_switch (vehicles, laptops, books)")
    updated_criteria: List[SlotValue] = Field(default_factory=list, description="Updated/new filter values for refine_filters or new_search")
    reasoning: str = Field(default="", description="Brief reasoning for classification")

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

    @classmethod
    def restore_from_session(cls, session_id: str, session_state) -> "UniversalAgent":
        """Reconstruct agent from persisted session state."""
        agent = cls(
            session_id=session_id,
            history=list(session_state.agent_history) if session_state.agent_history else [],
            max_questions=DEFAULT_MAX_QUESTIONS,
        )
        agent.domain = session_state.active_domain
        agent.filters = dict(session_state.agent_filters) if session_state.agent_filters else {}
        agent.questions_asked = list(session_state.agent_questions_asked) if session_state.agent_questions_asked else []
        agent.question_count = len(agent.questions_asked)
        if agent.domain:
            agent.state = AgentState.INTERVIEW
        return agent

    def get_state(self) -> Dict[str, Any]:
        """Return agent state as dict for session persistence."""
        return {
            "domain": self.domain,
            "filters": self.filters,
            "questions_asked": self.questions_asked,
            "question_count": self.question_count,
            "history": self.history[-10:],
        }

    def get_search_filters(self) -> Dict[str, Any]:
        """
        Convert agent's extracted criteria (slot names) into search-compatible filter format.

        For vehicles: budget → price range, brand → make, etc.
        For laptops/books: budget → price_min_cents/price_max_cents, etc.
        """
        search_filters = {}
        domain = self.domain or ""

        for slot_name, value in self.filters.items():
            if not value or str(value).lower() in ("no preference", "any", "either", "any price"):
                continue

            if slot_name == "budget":
                # Parse budget string into price filters
                budget_str = str(value).replace("$", "").replace(",", "").replace(" ", "")
                # Handle "k" suffix for vehicles (e.g. "20k-35k", "under 30k")
                budget_str = re.sub(r'(\d+)k', lambda m: str(int(m.group(1)) * 1000), budget_str, flags=re.IGNORECASE)

                range_match = re.match(r"(\d+)-(\d+)", budget_str)
                under_match = re.search(r"under(\d+)", budget_str.lower())
                over_match = re.search(r"over(\d+)", budget_str.lower())

                if domain == "vehicles":
                    if range_match:
                        search_filters["price"] = f"{range_match.group(1)}-{range_match.group(2)}"
                    elif under_match:
                        search_filters["price"] = f"0-{under_match.group(1)}"
                    elif over_match:
                        search_filters["price"] = f"{over_match.group(1)}-999999"
                else:
                    # E-commerce: use price_cents
                    if range_match:
                        search_filters["price_min_cents"] = int(range_match.group(1)) * 100
                        search_filters["price_max_cents"] = int(range_match.group(2)) * 100
                    elif under_match:
                        search_filters["price_max_cents"] = int(under_match.group(1)) * 100
                    elif over_match:
                        search_filters["price_min_cents"] = int(over_match.group(1)) * 100

            elif slot_name == "brand":
                if domain == "vehicles":
                    search_filters["make"] = value
                else:
                    search_filters["brand"] = value

            elif slot_name == "use_case":
                if domain == "vehicles":
                    search_filters["use_case"] = value
                else:
                    search_filters["subcategory"] = value
                    search_filters["use_case"] = value

            elif slot_name == "body_style":
                search_filters["body_style"] = value

            elif slot_name == "genre":
                search_filters["genre"] = value
                search_filters["subcategory"] = value

            elif slot_name == "format":
                search_filters["format"] = value

            elif slot_name == "item_type":
                search_filters["subcategory"] = value

            elif slot_name == "features":
                search_filters["_soft_preferences"] = {"liked_features": [value] if isinstance(value, str) else value}

            elif slot_name in ("fuel_type", "condition", "os", "screen_size", "color", "material"):
                search_filters[slot_name] = value

        return search_filters

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
                    "message": "I can help with Cars, Laptops, Books, or Phones. What are you looking for today?",
                    "quick_replies": ["Cars", "Laptops", "Books", "Phones"],
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
            # 5. Generate Question (LLM)
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
                model=OPENAI_MODEL,
                reasoning_effort=OPENAI_REASONING_EFFORT,
                messages=[
                    {"role": "system", "content": DOMAIN_DETECTION_PROMPT},
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
        Uses LLM to extract criteria based on the active schema.
        Also detects IDSS interview signals (impatience, recommendation requests).

        Input: User message + Schema Slots.
        Output: ExtractedCriteria with filters and signals.
        """
        try:
            # Construct a concise schema description for the LLM, including allowed values
            slots_desc = []
            for s in schema.slots:
                desc = f"- {s.name} ({s.description})"
                if s.allowed_values:
                    desc += f"\n  ALLOWED VALUES (use exactly one of these): {', '.join(s.allowed_values)}"
                slots_desc.append(desc)
            schema_text = "\n".join(slots_desc)

            price_context = PRICE_CONTEXT.get(schema.domain, "")

            system_prompt = CRITERIA_EXTRACTION_PROMPT.format(
                domain=schema.domain,
                schema_text=schema_text,
                price_context=price_context,
            )

            logger.info(f"Extracting criteria for domain: {schema.domain}")

            completion = self.client.beta.chat.completions.parse(
                model=OPENAI_MODEL,
                reasoning_effort=OPENAI_REASONING_EFFORT,
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
        Uses LLM to generate a natural follow-up question.

        IDSS Style:
        1. Main question about the slot topic
        2. Quick replies for that topic only
        3. ALWAYS end with invitation to share other topics at same priority level
        """
        try:
            # Build IDSS-style context
            slot_context = self._format_slot_context(slot, schema)
            assistant_type = DOMAIN_ASSISTANT_NAMES.get(schema.domain, schema.domain)

            system_prompt = QUESTION_GENERATION_PROMPT.format(
                assistant_type=assistant_type,
                slot_context=slot_context,
                slot_display_name=slot.display_name,
                slot_name=slot.name,
            )

            completion = self.client.beta.chat.completions.parse(
                model=OPENAI_MODEL,
                reasoning_effort=OPENAI_REASONING_EFFORT,
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

    def generate_recommendation_explanation(
        self, recommendations: List[List[Dict[str, Any]]], domain: str
    ) -> str:
        """
        Generate a conversational explanation of the recommendations,
        highlighting one standout product and why it matches the user's criteria.

        Works across all domains by adapting to whatever product fields are available.
        """
        # Build a compact summary of the products for the LLM
        product_summaries = []
        for row in recommendations:
            for product in row:
                summary = self._summarize_product(product, domain)
                if summary:
                    product_summaries.append(summary)
                if len(product_summaries) >= 6:
                    break
            if len(product_summaries) >= 6:
                break

        if not product_summaries:
            return f"Here are some {domain} recommendations based on your preferences."

        products_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(product_summaries))

        # What the user asked for
        if self.filters:
            criteria_text = ", ".join(f"{k}: {v}" for k, v in self.filters.items())
        else:
            criteria_text = "general browsing"

        try:
            system_prompt = RECOMMENDATION_EXPLANATION_PROMPT.format(domain=domain)

            completion = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                reasoning_effort=OPENAI_REASONING_EFFORT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"""User's preferences: {criteria_text}

Products found:
{products_text}

Write the recommendation message."""}
                ],
                temperature=0.7,
                max_tokens=200,
            )
            message = completion.choices[0].message.content.strip()
            logger.info(f"Generated recommendation explanation: {message[:80]}...")
            return message
        except Exception as e:
            logger.error(f"Recommendation explanation failed: {e}")
            return f"Here are top {domain} recommendations based on your preferences. What would you like to do next?"

    def process_refinement(self, message: str) -> Dict[str, Any]:
        """
        Classify and handle a post-recommendation message using LLM.

        Returns a dict with:
        - intent: refine_filters | domain_switch | new_search | action | other
        - For refine_filters: updated self.filters, returns recommendations_ready
        - For domain_switch: new_domain, signals caller to reset and re-route
        - For new_search: cleared filters + new criteria, returns recommendations_ready
        - For action/other: returns None (caller should handle or fall through)
        """
        try:
            filters_text = ", ".join(f"{k}: {v}" for k, v in self.filters.items()) if self.filters else "none"
            system_prompt = POST_REC_REFINEMENT_PROMPT.format(
                domain=self.domain or "unknown",
                filters=filters_text,
            )

            completion = self.client.beta.chat.completions.parse(
                model=OPENAI_MODEL,
                reasoning_effort=OPENAI_REASONING_EFFORT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                response_format=RefinementClassification,
            )
            result = completion.choices[0].message.parsed
            logger.info(f"Refinement classification: intent={result.intent}, reasoning={result.reasoning}")

            if result.intent == "refine_filters":
                # Merge updated criteria into existing filters
                for item in result.updated_criteria:
                    self.filters[item.slot_name] = item.value
                    logger.info(f"Refinement updated filter: {item.slot_name}={item.value}")
                self.history.append({"role": "user", "content": message})
                schema = get_domain_schema(self.domain)
                return self._handoff_to_search(schema)

            elif result.intent == "domain_switch":
                new_domain = result.new_domain
                if not new_domain or new_domain == "unknown":
                    new_domain = self._detect_domain_from_message(message)
                return {
                    "response_type": "domain_switch",
                    "new_domain": new_domain,
                    "message": message,
                }

            elif result.intent == "new_search":
                # Clear all filters and apply new criteria
                self.filters = {}
                self.questions_asked = []
                self.question_count = 0
                for item in result.updated_criteria:
                    self.filters[item.slot_name] = item.value
                self.history.append({"role": "user", "content": message})
                schema = get_domain_schema(self.domain)
                return self._handoff_to_search(schema)

            else:
                # "action" or "other" — not handled here
                return {"response_type": "not_refinement", "intent": result.intent}

        except Exception as e:
            logger.error(f"Refinement classification failed: {e}")
            return {"response_type": "not_refinement", "intent": "error"}

    def _summarize_product(self, product: Dict[str, Any], domain: str) -> Optional[str]:
        """Build a one-line summary of a product for LLM context."""
        if domain == "vehicles":
            v = product.get("vehicle", product)
            parts = []
            year = v.get("year") or product.get("year")
            make = v.get("make") or product.get("brand", "")
            model = v.get("model") or product.get("name", "")
            if year and make and model:
                parts.append(f"{year} {make} {model}")
            elif product.get("name"):
                parts.append(product["name"])
            trim = v.get("trim")
            if trim:
                parts.append(trim)
            price = v.get("price") or product.get("price", 0)
            if price:
                parts.append(f"${price:,}")
            body = v.get("bodyStyle") or v.get("norm_body_type", "")
            if body:
                parts.append(body)
            fuel = v.get("fuel") or v.get("norm_fuel_type", "")
            if fuel:
                parts.append(fuel)
            mpg_city = v.get("build_city_mpg") or v.get("city_mpg", 0)
            mpg_hwy = v.get("build_highway_mpg") or v.get("highway_mpg", 0)
            if mpg_city and mpg_hwy:
                parts.append(f"{mpg_city}/{mpg_hwy} MPG")
            mileage = v.get("mileage", 0)
            if mileage:
                parts.append(f"{mileage:,} mi")
            return " | ".join(parts) if parts else None

        # E-commerce (laptops, books)
        parts = []
        name = product.get("name", "")
        brand = product.get("brand", "")
        if name:
            parts.append(f"{brand} {name}" if brand and brand not in name else name)
        price = product.get("price", 0)
        if price:
            # Price might be in cents (from formatter) or dollars
            if price > 1000 and domain in ("books",):
                parts.append(f"${price/100:.2f}")
            else:
                parts.append(f"${price:,.0f}" if price > 100 else f"${price:.2f}")

        # Laptop-specific
        laptop = product.get("laptop", {})
        if laptop:
            specs = laptop.get("specs", {})
            spec_parts = [s for s in [specs.get("processor"), specs.get("ram"), specs.get("graphics")] if s]
            if spec_parts:
                parts.append(" / ".join(spec_parts))
            tags = laptop.get("tags", [])
            if tags:
                parts.append(", ".join(tags[:3]))

        # Book-specific
        book = product.get("book", {})
        if book:
            author = book.get("author")
            if author:
                parts.append(f"by {author}")
            genre = book.get("genre")
            if genre:
                parts.append(genre)
            fmt = book.get("format")
            if fmt:
                parts.append(fmt)

        return " | ".join(parts) if parts else None

    def _unknown_error_response(self):
        return {
            "response_type": "error",
            "message": "Something went wrong. Please try again.",
            "session_id": self.session_id
        }
