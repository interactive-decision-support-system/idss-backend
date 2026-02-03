"""
Universal Agent for IDSS.

This agent acts as the central brain for the Unified Pipeline.
It replaces the fragmented logic across `chat_endpoint.py`, `query_specificity.py`,
and `idss_adapter.py` with a single, schema-driven loop powered by LLMs.

Responsibilities:
1. Intent Detection (LLM - gpt-4o-mini)
2. State Management (Tracking filters and gathered info)
3. Criteria Extraction (LLM - gpt-4o-mini)
4. Question Generation (LLM - gpt-4o)
5. Handoff to Search
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
# "Basic" model for structural tasks
MODEL_BASIC = "gpt-4o-mini"
# "Powerful" model for natural language generation
MODEL_POWERFUL = "gpt-4o"

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
    """Structure for LLM extraction output."""
    criteria: List[SlotValue] = Field(description="List of extracted filter values")
    reasoning: str = Field(description="Brief reasoning for extraction")

class DomainClassification(BaseModel):
    """Structure for domain classification output."""
    domain: str = Field(description="One of: vehicles, laptops, books, unknown")
    confidence: float = Field(description="Confidence score 0-1")

class GeneratedQuestion(BaseModel):
    """Structure for question generation."""
    question: str = Field(description="The natural language question to ask")
    quick_replies: List[str] = Field(description="3-4 short suggestions for the user")

class UniversalAgent:
    def __init__(self, session_id: str, history: List[Dict[str, str]] = None):
        self.session_id = session_id
        self.history = history or []
        self.domain: Optional[str] = None
        self.filters: Dict[str, Any] = {}
        self.state = AgentState.INTENT_DETECTION
        
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
        
        # 2. Extract Criteria (Schema-Driven)
        schema = get_domain_schema(self.domain)
        if not schema:
            logger.error(f"No schema found for domain {self.domain}")
            return self._unknown_error_response()

        self._extract_criteria(message, schema)
        
        # 3. Check for Missing Information (Priority Check)
        missing_slot = self._get_next_missing_slot(schema)
        
        if missing_slot:
            # 4. Generate Question (LLM - Powerful)
            gen_q = self._generate_question(missing_slot, schema)
            
            response = {
                "response_type": "question",
                "message": gen_q.question,
                "quick_replies": gen_q.quick_replies,
                "session_id": self.session_id,
                "domain": self.domain,
                "filters": self.filters
            }
            self.history.append({"role": "assistant", "content": response["message"]})
            return response
        
        else:
            # 5. Ready for Search
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

    def _extract_criteria(self, message: str, schema: DomainSchema):
        """
        Uses LLM (Basic Model) to extract criteria based on the active schema.
        Input: User message + Schema Slots.
        Output: JSON dict of extracted filters.
        """
        try:
            # Construct a concise schema description for the LLM
            slots_desc = [f"- {s.name} ({s.description})" for s in schema.slots]
            schema_text = "\n".join(slots_desc)
            
            system_prompt = f"""You are a smart extraction agent for the '{schema.domain}' domain.
            Your goal is to extract specific criteria from the user's message based on the available slots:
            {schema_text}
            
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
                
        except Exception as e:
            logger.error(f"Criteria extraction failed: {e}")

    def _get_next_missing_slot(self, schema: DomainSchema) -> Optional[PreferenceSlot]:
        """
        Determines the next question to ask based on Priority.
        High -> Medium -> Low.
        """
        slots_by_priority = schema.get_slots_by_priority()
        
        # Check HIGH priority first
        for slot in slots_by_priority[SlotPriority.HIGH]:
            if slot.name not in self.filters:
                return slot
        
        # Check MEDIUM priority
        for slot in slots_by_priority[SlotPriority.MEDIUM]:
            if slot.name not in self.filters:
                return slot
        
        # LOW Priorities - strictly optional
        # Only ask if we barely have info? 
        # For now, allow SEARCH if all High/Med are filled.
        return None

    def _generate_question(self, slot: PreferenceSlot, schema: DomainSchema) -> GeneratedQuestion:
        """
        Uses LLM (Powerful Model) to generate a natural follow-up question.
        """
        try:
            system_prompt = f"""You are a helpful expert assistant for {schema.domain}.
            You need to ask the user about their '{slot.display_name}'.
            
            Context so far:
            {json.dumps(self.filters, indent=2)}
            
            Goal: Ask a natural, polite question to get information about '{slot.name}'.
            Also provide 3-4 short 'quick reply' options for the user.
            """
            
            completion = self.client.beta.chat.completions.parse(
                model=MODEL_POWERFUL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    # Include recent history for conversational flow context
                    *self.history[-3:] 
                ],
                response_format=GeneratedQuestion,
            )
            return completion.choices[0].message.parsed
            
        except Exception as e:
            logger.error(f"Question generation failed: {e}")
            # Fallback to schema static data
            return GeneratedQuestion(
                question=slot.example_question,
                quick_replies=slot.example_replies
            )

    def _handoff_to_search(self, schema: DomainSchema) -> Dict[str, Any]:
        """
        Constructs the search response/handoff.
        """
        response = {
            "response_type": "recommendations_ready", 
            "message": "Searching for the perfect match...",
            "session_id": self.session_id,
            "domain": self.domain,
            "filters": self.filters,
            "schema_used": schema.domain
        }
        self.history.append({"role": "assistant", "content": response["message"]})
        return response

    def _unknown_error_response(self):
        return {
            "response_type": "error",
            "message": "Something went wrong. Please try again.",
            "session_id": self.session_id
        }
