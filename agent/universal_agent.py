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

# ---------------------------------------------------------------------------
# Slot-name normalisation: map common LLM alias → canonical schema slot name.
# The LLM may return "ram" instead of "min_ram_gb", "price" instead of "budget",
# etc. Without normalisation those end up in self.filters under wrong keys and
# _get_next_missing_slot() still sees them as missing, re-asking the user.
# ---------------------------------------------------------------------------
_SLOT_NAME_ALIASES: Dict[str, Dict[str, str]] = {
    "laptops": {
        "ram": "min_ram_gb",
        "ram_gb": "min_ram_gb",
        "min_ram": "min_ram_gb",
        "memory": "min_ram_gb",
        "min_memory": "min_ram_gb",
        "min_memory_gb": "min_ram_gb",
        "memory_gb": "min_ram_gb",
        "price": "budget",
        "price_range": "budget",
        "max_price": "budget",
        "cost": "budget",
        "price_max": "budget",
        "screen": "screen_size",
        "display": "screen_size",
        "display_size": "screen_size",
        "screen_inches": "screen_size",
        "monitor_size": "screen_size",
        "storage": "storage_type",
        "disk_type": "storage_type",
        "drive_type": "storage_type",
        "excluded_brand": "excluded_brands",
        "excluded_brand_list": "excluded_brands",
        "brands_to_exclude": "excluded_brands",
        "avoid_brands": "excluded_brands",
        "operating_system": "os",
        "preferred_os": "os",
    },
    "vehicles": {
        "price": "budget",
        "price_range": "budget",
        "max_price": "budget",
        "make": "brand",
        "manufacturer": "brand",
        "style": "body_style",
        "car_type": "body_style",
        "fuel": "fuel_type",
        "engine_type": "fuel_type",
    },
    "books": {
        "price": "budget",
        "category": "genre",
        "type": "genre",
        "book_type": "format",
    },
}

# Model configuration — single model for all LLM calls, set via environment
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
# Default is "" (disabled). Set OPENAI_REASONING_EFFORT=low in .env only if using an o-series model.
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "")
_REASONING_KWARGS = {"reasoning_effort": OPENAI_REASONING_EFFORT} if OPENAI_REASONING_EFFORT else {}

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
    def __init__(self, session_id: str, history: Optional[List[Dict[str, str]]] = None, max_questions: int = DEFAULT_MAX_QUESTIONS):
        self.session_id = session_id
        self.history: List[Any] = history or []
        self.domain: Optional[str] = None
        self.filters: Dict[str, Any] = {}
        self.state = AgentState.INTENT_DETECTION

        # IDSS interview state
        self.question_count = 0
        self.max_questions = max_questions
        self.questions_asked: List[str] = []  # Slot names we've asked about

        # Initialize OpenAI client.
        # timeout=10s: fail fast if OpenAI is slow rather than blocking for 600s.
        # max_retries=0: don't auto-retry on 429 (Retry-After can be 30s+);
        #   the except blocks fall back to regex immediately instead.
        self.client = OpenAI(timeout=10.0, max_retries=0)

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
                        # Plain number (e.g. '35000') → treat as max price
                        plain = re.search(r'(\d+)', budget_str)
                        if plain:
                            search_filters["price"] = f"0-{plain.group(1)}"
                else:
                    # E-commerce: use price_cents
                    if range_match:
                        # Only set the ceiling for e-commerce ranges — "$1500-$2000" means
                        # "up to $2000". A strict floor would exclude near-miss products
                        # (e.g. Apple laptops at $1399 for a "$1500-$2000" budget).
                        # The product store's quality floor already provides a soft lower bound.
                        search_filters["price_max_cents"] = int(range_match.group(2)) * 100
                    elif under_match:
                        search_filters["price_max_cents"] = int(under_match.group(1)) * 100
                    elif over_match:
                        search_filters["price_min_cents"] = int(over_match.group(1)) * 100
                    else:
                        # Plain number (e.g. '2000' from '$2000') → treat as max price
                        plain = re.search(r'(\d+)', budget_str)
                        if plain:
                            search_filters["price_max_cents"] = int(plain.group(1)) * 100

            elif slot_name == "brand":
                if domain == "vehicles":
                    search_filters["make"] = value
                else:
                    search_filters["brand"] = value

            elif slot_name == "use_case":
                if domain == "vehicles":
                    search_filters["use_case"] = value
                elif domain == "books":
                    search_filters["subcategory"] = value
                else:
                    # For electronics/laptops: map to good_for_* boolean attribute
                    use_lower = str(value).lower()
                    if any(k in use_lower for k in ("gaming", "game")):
                        search_filters["good_for_gaming"] = True
                    elif any(k in use_lower for k in ("ml", "machine learning", "ai", "deep learning", "pytorch")):
                        search_filters["good_for_ml"] = True
                    elif any(k in use_lower for k in ("creative", "design", "video", "photo", "art")):
                        search_filters["good_for_creative"] = True
                    elif any(k in use_lower for k in ("web", "dev", "develop")):
                        search_filters["good_for_web_dev"] = True
                    # Always keep as soft preference for ranking
                    search_filters.setdefault("_soft_preferences", {})["use_case"] = value

            elif slot_name == "min_ram_gb":
                # LLM may return '16 GB', '16', '16gb', etc. — extract the number
                m = re.search(r'(\d+)', str(value))
                if m:
                    search_filters["min_ram_gb"] = int(m.group(1))

            elif slot_name in ("screen_size", "min_screen_size"):
                raw = str(value).lower().strip()
                # Extract all numbers present (handles "14-16", "14 to 16", "15.6", etc.)
                nums = [float(n) for n in re.findall(r'\d+\.?\d*', raw)]

                if not nums:
                    # No digits: check for size-implying words and use canonical defaults.
                    # e.g. "small", "compact", "portable", "travel" → max 14"
                    if any(w in raw for w in ("small", "compact", "portable", "travel", "mini", "ultrabook")):
                        search_filters["max_screen_size"] = 14.0
                    elif any(w in raw for w in ("large", "big", "wide", "17")):
                        search_filters["min_screen_size"] = 15.0
                    # else: truly unparseable — skip
                elif any(w in raw for w in ("under", "less", "small", "compact", "below", "max", "up to", "at most")):
                    # User wants a small/compact screen — apply as maximum
                    search_filters["max_screen_size"] = nums[0]
                elif any(w in raw for w in ("at least", "minimum", "min", "or larger", "larger", "bigger", "over", "above")):
                    # User wants a minimum screen size
                    search_filters["min_screen_size"] = nums[0]
                elif len(nums) == 2:
                    # Explicit range: "14 to 16" or "14-16"
                    search_filters["min_screen_size"] = min(nums)
                    search_filters["max_screen_size"] = max(nums)
                else:
                    # Exact value: apply ±0.5" tolerance
                    search_filters["min_screen_size"] = nums[0] - 0.5
                    search_filters["max_screen_size"] = nums[0] + 0.5

            elif slot_name == "storage_type":
                val_str = str(value).upper().strip().split()[0]  # 'SSD (fast)' → 'SSD'
                if val_str in ("SSD", "HDD"):
                    search_filters["storage_type"] = val_str

            elif slot_name == "body_style":
                search_filters["body_style"] = value

            elif slot_name == "genre":
                search_filters["genre"] = value
                search_filters["subcategory"] = value

            elif slot_name == "format":
                search_filters["format"] = value

            elif slot_name == "product_type":
                search_filters["product_type"] = value

            elif slot_name == "item_type":
                search_filters["subcategory"] = value

            elif slot_name == "features":
                search_filters["_soft_preferences"] = {"liked_features": [value] if isinstance(value, str) else value}

            elif slot_name == "excluded_brands":
                # Comma-separated list of brands to EXCLUDE, e.g. "HP,Acer"
                raw = str(value).strip()
                brands = [b.strip() for b in re.split(r"[,;/|]", raw) if b.strip()]
                if brands:
                    search_filters["excluded_brands"] = brands
                    logger.info(f"Excluded brands: {brands}")

            elif slot_name == "os":
                search_filters["os"] = value

            elif slot_name in ("fuel_type", "condition", "screen_size", "color", "material"):
                search_filters[slot_name] = value

        return search_filters

    def process_message(self, message: str) -> Dict[str, Any]:
        import time
        timings = {}
        t0 = time.perf_counter()
        self.history.append({"role": "user", "content": message})

        # 1. Domain Detection — run when domain is unknown, or when message contains
        # fast-map keywords that clearly indicate a different domain (zero-latency switch).
        t1 = time.perf_counter()
        if self.domain:
            # Check for domain switch via fast map (no LLM call, O(n) word scan).
            # e.g. session domain="vehicles" but user says "school laptops" → switch to "laptops".
            words = message.strip().lower().split()
            for w in words:
                candidate = self._FAST_DOMAIN_MAP.get(w)
                if candidate and candidate != self.domain:
                    logger.info(f"Fast-map domain switch: '{w}' → {candidate} (was {self.domain})")
                    self.domain = candidate
                    self.filters = {}
                    self.questions_asked = []
                    self.question_count = 0
                    break
        else:
            if self.history and len(self.history) > 1:
                # Multi-turn session with no domain set: recover from history keyword scan,
                # then re-run detection on the current message if still unknown.
                self.domain = self._detect_domain_from_history()
                if not self.domain:
                    self.domain = self._detect_domain_from_message(message=message)
            else:
                self.domain = self._detect_domain_from_message(message=message)
        timings["domain_detection_ms"] = (time.perf_counter() - t1) * 1000
        if not self.domain or self.domain == "unknown":
            # Still unknown, ask for clarification
            response = {
                "response_type": "question",
                "message": "I can help with Cars, Laptops, Books, or Phones. What are you looking for today?",
                "quick_replies": ["Cars", "Laptops", "Books", "Phones"],
                "session_id": self.session_id,
                "timings_ms": timings
            }
            self.history.append({"role": "assistant", "content": response["message"]})
            # Reset domain so we try again next time
            self.domain = None
            return response

        # 2. Extract Criteria (Schema-Driven) with IDSS signals
        schema = get_domain_schema(self.domain)
        if not schema:
            logger.error(f"No schema found for domain {self.domain}")
            resp = self._unknown_error_response()
            resp["timings_ms"] = timings
            return resp

        t2 = time.perf_counter()
        extraction_result = self._extract_criteria(message, schema)
        timings["criteria_extraction_ms"] = (time.perf_counter() - t2) * 1000

        # 3. Check IDSS interview signals - should we skip to recommendations?
        if self._should_recommend(extraction_result, schema):
            logger.info(f"Skipping to recommendations (impatient={extraction_result.is_impatient if extraction_result else False}, "
                       f"wants_recs={extraction_result.wants_recommendations if extraction_result else False}, "
                       f"question_count={self.question_count}/{self.max_questions})")
            resp = self._handoff_to_search(schema)
            resp["timings_ms"] = timings
            return resp

        # 4. Check for Missing Information (Priority Check)
        missing_slot = self._get_next_missing_slot(schema)

        if missing_slot:
            # 5. Generate Question (LLM)
            t3 = time.perf_counter()
            gen_q = self._generate_question(missing_slot, schema)
            timings["question_generation_ms"] = (time.perf_counter() - t3) * 1000

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
                "question_count": self.question_count,
                "topic": gen_q.topic,
                "timings_ms": timings
            }
            self.history.append({"role": "assistant", "content": response["message"]})
            return response

        # All HIGH+MEDIUM slots are filled — no more questions to ask
        # Proceed to search even if we haven't hit the question limit
        logger.info("All slots filled — handing off to search")
        resp = self._handoff_to_search(schema)
        resp["timings_ms"] = timings
        return resp

    # Fast keyword lookup — any word in the user's message matched here skips the LLM call.
    # Keys are lowercased single tokens; values are domain names.
    _FAST_DOMAIN_MAP = {
        # ── Vehicles ───────────────────────────────────────────────────────────
        "cars": "vehicles", "car": "vehicles", "vehicle": "vehicles", "vehicles": "vehicles",
        "autos": "vehicles", "auto": "vehicles", "truck": "vehicles", "trucks": "vehicles",
        "suv": "vehicles", "sedan": "vehicles", "van": "vehicles", "minivan": "vehicles",
        "pickup": "vehicles", "hatchback": "vehicles", "coupe": "vehicles", "convertible": "vehicles",
        "horsepower": "vehicles", "mpg": "vehicles", "dealership": "vehicles", "ev": "vehicles",
        # ── Laptops / Electronics ──────────────────────────────────────────────
        "laptops": "laptops", "laptop": "laptops", "electronics": "laptops",
        "computers": "laptops", "computer": "laptops", "desktop": "laptops", "desktops": "laptops",
        "notebook": "laptops", "chromebook": "laptops", "ultrabook": "laptops",
        # Common typos
        "latop": "laptops", "labtop": "laptops", "lapop": "laptops", "lpatop": "laptops",
        # Gaming titles clearly imply a computer
        "minecraft": "laptops", "roblox": "laptops", "fortnite": "laptops",
        # Apple / brand model names
        "macbook": "laptops", "mac": "laptops", "imac": "laptops",
        "thinkpad": "laptops", "surface": "laptops", "xps": "laptops",
        # OS names clearly signal a computer query
        "windows": "laptops", "linux": "laptops", "ubuntu": "laptops", "macos": "laptops",
        # Hardware specs that only appear in computer queries
        "ram": "laptops", "ssd": "laptops", "nvme": "laptops", "hdd": "laptops",
        "gpu": "laptops", "cpu": "laptops", "processor": "laptops",
        "nvidia": "laptops", "rtx": "laptops", "gtx": "laptops", "radeon": "laptops",
        "intel": "laptops", "amd": "laptops", "ryzen": "laptops",
        "monitor": "laptops", "display": "laptops",
        "pytorch": "laptops", "tensorflow": "laptops", "cuda": "laptops",
        # Phones (map to laptops domain — same electronics DB)
        "phones": "laptops", "phone": "laptops", "smartphone": "laptops", "smartphones": "laptops",
        "mobile": "laptops", "mobiles": "laptops",
        "iphone": "laptops", "android": "laptops", "pixel": "laptops",
        # ── Books ──────────────────────────────────────────────────────────────
        "books": "books", "book": "books", "reading": "books", "novel": "books",
        "author": "books", "fiction": "books", "nonfiction": "books", "paperback": "books",
        "audiobook": "books", "kindle": "books", "genre": "books",
    }

    def _detect_domain_from_message(self, message: str) -> Optional[str]:
        """
        Classify the user's message into a domain.

        Detection order (fastest → most accurate):
        1. Word-scan the message against _FAST_DOMAIN_MAP (no LLM, ~0 ms).
        2. LLM structured-output parse (most accurate, ~300 ms).
        3. If LLM fails: keyword scan of the full message text as final fallback.
        """
        # ── 1. Word-scan fast path ──────────────────────────────────────────────
        # Scan EACH word in the message, not the whole string. This catches
        # multi-word queries like "i want a macbook" and "windows 10 laptop".
        cleaned = re.sub(r"[^a-z0-9]", " ", message.lower())
        for word in cleaned.split():
            fast = self._FAST_DOMAIN_MAP.get(word)
            if fast:
                logger.info(f"Fast domain keyword: '{word}' → {fast}")
                return fast

        # ── 2. LLM classification ───────────────────────────────────────────────
        try:
            logger.info(f"Detecting domain via LLM for: {message[:60]}...")

            completion = self.client.beta.chat.completions.parse(
                model=OPENAI_MODEL,
                **_REASONING_KWARGS,
                max_completion_tokens=64,   # domain + confidence only — tiny JSON
                messages=[
                    {"role": "system", "content": DOMAIN_DETECTION_PROMPT},
                    {"role": "user", "content": message}
                ],
                response_format=DomainClassification,
            )
            result = completion.choices[0].message.parsed
            if not result:
                logger.warning("Domain detection: LLM returned None (parsing failed)")
            elif result.domain and result.domain != "unknown":
                logger.info(f"Domain detected via LLM: {result.domain} (conf: {result.confidence})")
                return result.domain

        except Exception as e:
            logger.error(f"Domain detection LLM call failed: {e}")

        # ── 3. Fallback: broader keyword scan over raw text ─────────────────────
        text = message.lower()
        vehicle_kws = ("car", "truck", "suv", "sedan", "van", "vehicle", "driving", "mpg", "horsepower", "dealership")
        laptop_kws  = ("laptop", "computer", "macbook", "notebook", "windows", "linux", "macos",
                       "ram", "ssd", "gpu", "cpu", "processor", "nvidia", "intel", "amd", "ryzen",
                       "pytorch", "tensorflow", "coding", "programming", "phone", "tablet", "ipad",
                       "data science", "machine learning", "minecraft", "gaming", "rugged",
                       "latop", "labtop", "student", "school", "college", "class")
        book_kws    = ("book", "novel", "fiction", "author", "read", "genre", "paperback", "kindle")

        v_hits = sum(1 for k in vehicle_kws if k in text)
        l_hits = sum(1 for k in laptop_kws  if k in text)
        b_hits = sum(1 for k in book_kws    if k in text)

        best = max(v_hits, l_hits, b_hits)
        if best > 0:
            domain = "vehicles" if v_hits == best else "laptops" if l_hits == best else "books"
            logger.info(f"Domain detected via keyword fallback: {domain} (v={v_hits} l={l_hits} b={b_hits})")
            return domain

        return None

    def _detect_domain_from_history(self) -> Optional[str]:
        """
        Recover domain from conversation history WITHOUT making a new LLM call.
        This is a safety net — in normal flows the domain is always restored from
        the session object and this method should never be reached.
        Scans the prior history text for known domain keywords.
        """
        history_text = " ".join(
            m.get("content", "") for m in self.history if m.get("role") == "user"
        ).lower()

        vehicle_hits = sum(history_text.count(k) for k in (
            "car", "truck", "suv", "sedan", "van", "vehicle", "auto", "driving", "mpg", "vin", "horsepower",
        ))
        laptop_hits  = sum(history_text.count(k) for k in (
            "laptop", "computer", "macbook", "notebook", "chromebook", "desktop",
            "windows", "linux", "macos", "ubuntu",
            "gpu", "ram", "ssd", "nvme", "cpu", "processor",
            "nvidia", "intel", "amd", "ryzen", "rtx", "gtx",
            "pytorch", "tensorflow", "cuda", "coding", "programming",
            "phone", "smartphone", "tablet", "ipad", "iphone", "android",
        ))
        book_hits    = sum(history_text.count(k) for k in (
            "book", "novel", "read", "author", "fiction", "genre", "paperback", "kindle",
        ))

        best = max(vehicle_hits, laptop_hits, book_hits)
        if best == 0:
            return None
        if vehicle_hits == best:
            logger.info("Domain recovered from history keywords: vehicles")
            return "vehicles"
        if laptop_hits == best:
            logger.info("Domain recovered from history keywords: laptops")
            return "laptops"
        logger.info("Domain recovered from history keywords: books")
        return "books"



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
                    desc += f"\n  KNOWN VALUES (prefer these; if the user explicitly names a specific value not in this list, extract it as stated): {', '.join(s.allowed_values)}"
                slots_desc.append(desc)
            schema_text = "\n".join(slots_desc)

            price_context = PRICE_CONTEXT.get(schema.domain, "")

            system_prompt = CRITERIA_EXTRACTION_PROMPT.format(
                domain=schema.domain,
                schema_text=schema_text,
                price_context=price_context,
            )

            logger.info(f"Extracting criteria for domain: {schema.domain}")

            # Include recent conversation history so the LLM can pick up criteria
            # mentioned in earlier turns (e.g. budget stated on turn 1 is still
            # visible when processing the user's answer to Q1 on turn 2).
            history_msgs = [
                m for m in self.history[-6:]
                if m.get("role") in ("user", "assistant") and m.get("content") != message
            ]

            completion = self.client.beta.chat.completions.parse(
                model=OPENAI_MODEL,
                **_REASONING_KWARGS,
                max_completion_tokens=512,  # criteria list JSON — never needs more than ~100 tokens
                messages=[
                    {"role": "system", "content": system_prompt},
                    *history_msgs,
                    {"role": "user", "content": message}
                ],
                response_format=ExtractedCriteria,
            )
            result = completion.choices[0].message.parsed
            if not result:
                logger.warning("Criteria extraction returned None")
                return None

            # Merge extracted filters into state — normalise slot names first.
            # The LLM sometimes returns "ram" / "price" instead of the canonical
            # schema slot names "min_ram_gb" / "budget".  Map them back so that
            # _get_next_missing_slot() correctly sees them as already filled.
            if result.criteria:
                domain_aliases = _SLOT_NAME_ALIASES.get(schema.domain, {})
                # Build valid slot name set from schema — prevents spurious slots like
                # min_year=2024 (from "best laptop 2024") from polluting the filters.
                _schema_slot_names = {s.name for s in schema.slots}
                # Extra slots that are valid but not in the schema definition.
                _ALWAYS_ALLOW = {
                    "excluded_brands", "_soft_preferences",
                    "good_for_gaming", "good_for_creative",
                    "good_for_web_dev", "good_for_ml",
                    "use_case",
                }
                new_filters: Dict[str, Any] = {}
                for item in result.criteria:
                    canonical = domain_aliases.get(item.slot_name, item.slot_name)
                    if (
                        canonical in _schema_slot_names
                        or canonical in _ALWAYS_ALLOW
                        or canonical.startswith("good_for_")
                    ):
                        new_filters[canonical] = item.value
                    else:
                        logger.info(
                            f"Dropping unknown slot '{item.slot_name}' "
                            f"(canonical='{canonical}') — not in schema for domain '{schema.domain}'"
                        )
                logger.info(f"Extracted filters (normalised): {new_filters}")
                self.filters.update(new_filters)

            # Log IDSS signals
            if result.is_impatient:
                logger.info("User is impatient - will skip to recommendations")
            if result.wants_recommendations:
                logger.info("User explicitly wants recommendations")

            return result

        except Exception as e:
            logger.error(f"Criteria extraction failed: {e} — falling back to regex extractor")
            return self._regex_extract_criteria(message, schema)

    # ------------------------------------------------------------------
    # Regex-based fallback extractor.
    # Used when the LLM call fails (quota exhausted, network error, etc.).
    # Handles the most common slot patterns for all domains.
    # ------------------------------------------------------------------
    def _regex_extract_criteria(self, message: str, schema: DomainSchema) -> Optional[ExtractedCriteria]:
        """
        Rule-based extraction for the most common slot patterns.
        This is a resilience fallback — the LLM produces richer results.
        """
        text = message.lower()
        criteria: List[SlotValue] = []
        domain = schema.domain

        # ── Budget ───────────────────────────────────────────────────────────
        # Patterns: "$900", "under $900", "budget $900", "$800-$1,500", "500 bucks"
        budget_val: Optional[str] = None
        _b_range = re.search(r'\$(\d[\d,]*)\s*[-–to]+\s*\$?(\d[\d,]*k?)', text, re.IGNORECASE)
        _b_under = re.search(
            r'(?:under|below|less than|at most|up to|max|budget[:\s]+)\s*\$\s*(\d[\d,]*)',
            text, re.IGNORECASE
        )
        _b_plain = re.search(r'\$\s*(\d[\d,]+)', text)
        # "500 bucks", "500 dollars", "500 usd" (without dollar sign)
        _b_bucks = re.search(
            r'(?:^|[\s,])(\d{2,5})\s*(?:bucks?|dollars?|usd)\b',
            text, re.IGNORECASE
        )
        if _b_range:
            lo = _b_range.group(1).replace(",", "")
            hi = _b_range.group(2).replace(",", "").rstrip("k")
            if "k" in _b_range.group(2):
                hi = str(int(hi) * 1000)
            budget_val = f"${lo}-${hi}"
        elif _b_under:
            budget_val = f"${_b_under.group(1).replace(',', '')}"
        elif _b_plain:
            budget_val = f"${_b_plain.group(1).replace(',', '')}"
        elif _b_bucks:
            budget_val = f"${_b_bucks.group(1)}"
        if budget_val:
            criteria.append(SlotValue(slot_name="budget", value=budget_val))

        # ── RAM (laptops / phones) ────────────────────────────────────────────
        _ram = re.search(
            r'(?:at\s+least\s+)?(\d{1,3})\s*(?:gb|g)\s*(?:of\s+)?(?:ram|memory)',
            text, re.IGNORECASE
        )
        if not _ram:
            _ram = re.search(r'(\d{1,3})\s*(?:gigs?)\s*(?:of\s+)?(?:ram|memory)?', text, re.IGNORECASE)
        if _ram:
            val_gb = int(_ram.group(1))
            if 2 <= val_gb <= 256:
                criteria.append(SlotValue(slot_name="min_ram_gb", value=str(val_gb)))

        # ── Brand exclusions ─────────────────────────────────────────────────
        # Matches: "no HP", "not HP", "anything but HP", "avoid HP", "hate HP",
        #          "no HP or Acer", "no HP, no Acer"
        _known_brands = [
            "HP", "Acer", "Dell", "Lenovo", "Apple", "ASUS", "Asus",
            "MSI", "Razer", "Samsung", "Microsoft", "LG", "Gigabyte",
            "Framework", "System76", "ROG", "Alienware",
        ]
        _excl_kw_pat = re.compile(
            r'(?:no|not|never|anything but|avoid|hate|refuse|bad|terrible|skip)\s+([A-Za-z][A-Za-z0-9\- ]{1,30})',
            re.IGNORECASE
        )
        excl_brands: List[str] = []
        for _m in _excl_kw_pat.finditer(message):
            # Group can be "HP or Acer" or "HP, no Acer" — split on or/and/, to get each brand
            raw_group = _m.group(1).strip()
            parts = re.split(r'\s+(?:or|and)\s+|[,;]\s*', raw_group)
            for part in parts:
                candidate = part.strip().split()[0]  # first word of each part
                for brand in _known_brands:
                    if brand.lower() == candidate.lower():
                        if brand not in excl_brands:
                            excl_brands.append(brand)
        if excl_brands:
            criteria.append(SlotValue(slot_name="excluded_brands", value=",".join(excl_brands)))

        # ── OS ───────────────────────────────────────────────────────────────
        _os_map = [
            (re.compile(r'\bwindows\s*10\b', re.I), "Windows 10"),
            (re.compile(r'\bwindows\s*11\b', re.I), "Windows 11"),
            (re.compile(r'\bwindows\b', re.I),      "Windows 11"),
            (re.compile(r'\blinux\b|\bubuntu\b|\bdebian\b|\bfedora\b', re.I), "Linux"),
            (re.compile(r'\bmacos\b|\bos\s*x\b|\bapple\s+os\b', re.I), "macOS"),
            (re.compile(r'\bchrome\s*os\b|\bchromebook\b', re.I), "Chrome OS"),
        ]
        for _pat, _os_val in _os_map:
            if _pat.search(message):
                criteria.append(SlotValue(slot_name="os", value=_os_val))
                break

        # ── Screen size ───────────────────────────────────────────────────────
        _scr = re.search(
            r'(\d{2}(?:\.\d)?)\s*(?:"|″|inch(?:es)?|-inch)(?:\s+(?:screen|display|laptop))?',
            text, re.IGNORECASE
        )
        if _scr:
            criteria.append(SlotValue(slot_name="screen_size", value=_scr.group(1)))

        # ── Storage type ──────────────────────────────────────────────────────
        if re.search(r'\bssd\b', text):
            criteria.append(SlotValue(slot_name="storage_type", value="SSD"))
        elif re.search(r'\bhdd\b|\bhard\s+drive\b', text):
            criteria.append(SlotValue(slot_name="storage_type", value="HDD"))

        # ── Use-case (laptops) ────────────────────────────────────────────────
        if domain == "laptops":
            _use_map = [
                (r'\bgaming\b', "gaming"),
                (r'\bml\b|\bmachine\s+learning\b|\bai\b|\bdeep\s+learning\b|\bpytorch\b|\btensorflow\b', "machine_learning"),
                (r'\bcreative\b|\bdesign\b|\bvideo\s+edit\b|\bphoto\s+edit\b|\bfigma\b', "creative"),
                (r'\bweb\s*dev\b|\bprogramm\b|\bcod(e|ing)\b|\bsoftware\s+dev\b', "web_dev"),
                (r'\bschool\b|\bstudent\b|\bcollege\b|\bstud(y|ying)\b', "school"),
                (r'\bwork\b|\bbusiness\b|\boffice\b|\bprofessional\b', "business"),
            ]
            for _uc_pat, _uc_val in _use_map:
                if re.search(_uc_pat, text, re.IGNORECASE):
                    criteria.append(SlotValue(slot_name="use_case", value=_uc_val))
                    break

        # ── Preferred brand ───────────────────────────────────────────────────
        # Only extract if NOT negated (i.e., not in excl_brands)
        _brand_phrases = [
            (r'\b(?:apple|macbook|mac\s+air|mac\s+pro)\b', "Apple"),
            (r'\bdell\b|\bxps\b|\binspiron\b|\blatitude\b', "Dell"),
            (r'\blenovo\b|\bthinkpad\b|\bideapad\b', "Lenovo"),
            (r'\basus\b|\brog\b', "ASUS"),
            (r'\bmsi\b', "MSI"),
            (r'\brazer\b', "Razer"),
            (r'\bmicrosoft\b|\bsurface\b', "Microsoft"),
            (r'\bsamsung\b', "Samsung"),
            (r'\bframework\b', "Framework"),
            (r'\bsystem76\b', "System76"),
            (r'\bhp\b|\bhewlett\b', "HP"),
            (r'\bacer\b|\baspire\b|\bswift\b', "Acer"),
            (r'\bgigabyte\b|\baorus\b', "Gigabyte"),
            (r'\btoshiba\b|\bdynabook\b', "Toshiba"),
        ]
        # Only set if user is NOT excluding the brand
        for _bp, _bv in _brand_phrases:
            if re.search(_bp, text, re.IGNORECASE) and _bv not in excl_brands:
                # Check not in an exclusion context
                _mctx = re.search(
                    r'(?:no|not|avoid|hate)\s+' + re.escape(_bv.lower()),
                    text, re.IGNORECASE
                )
                if not _mctx:
                    criteria.append(SlotValue(slot_name="brand", value=_bv))
                    break

        # ── Intent signals ────────────────────────────────────────────────────
        _impatient_kws = (
            "just show", "show me results", "skip", "don't care", "doesn't matter",
            "whatever", "anything works", "surprise me",
        )
        _rec_kws = (
            "show me options", "show me some", "what do you recommend",
            "give me recommendations", "show me laptop", "show me the best",
            "let's see", "let me see", "find me",
        )
        is_impatient = any(kw in text for kw in _impatient_kws)
        wants_recs = any(kw in text for kw in _rec_kws)
        # Heuristic: if ≥2 substantive criteria extracted and no question mark,
        # the user is giving us their requirements — recommend without more questions.
        substantive = [c for c in criteria if c.slot_name not in ("os",)]
        if not wants_recs and len(substantive) >= 2 and "?" not in message:
            wants_recs = True

        if criteria:
            domain_aliases = _SLOT_NAME_ALIASES.get(domain, {})
            new_filters: Dict[str, Any] = {}
            for item in criteria:
                canonical = domain_aliases.get(item.slot_name, item.slot_name)
                new_filters[canonical] = item.value
            logger.info(f"Regex fallback extracted (normalised): {new_filters}")
            self.filters.update(new_filters)
        else:
            logger.info("Regex fallback: no criteria found")

        return ExtractedCriteria(
            criteria=criteria,
            reasoning="regex fallback (LLM unavailable)",
            is_impatient=is_impatient,
            wants_recommendations=wants_recs,
        )

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

    # Slots that are EXTRACT-ONLY — never ask the user about them.
    # They are populated only when the user explicitly states them.
    _EXTRACT_ONLY_SLOTS = frozenset({"excluded_brands", "os"})

    def _get_next_missing_slot(self, schema: DomainSchema) -> Optional[PreferenceSlot]:
        """
        Determines the next question to ask based on Priority.
        HIGH -> MEDIUM -> LOW (but respects questions already asked).
        Extract-only slots (excluded_brands, os) are never returned here.
        """
        slots_by_priority = schema.get_slots_by_priority()

        # Check HIGH priority first
        for slot in slots_by_priority[SlotPriority.HIGH]:
            if slot.name in self._EXTRACT_ONLY_SLOTS:
                continue
            if slot.name not in self.filters and slot.name not in self.questions_asked:
                return slot

        # Check MEDIUM priority
        for slot in slots_by_priority[SlotPriority.MEDIUM]:
            if slot.name in self._EXTRACT_ONLY_SLOTS:
                continue
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
                **_REASONING_KWARGS,
                max_completion_tokens=256,  # one question + 4 quick replies — never large
                messages=[
                    {"role": "system", "content": system_prompt},
                    # Include recent history for conversational flow context
                    *self.history[-3:]
                ],
                response_format=GeneratedQuestion
            )
            result = completion.choices[0].message.parsed
            if not result:
                raise ValueError("Question generation parsing returned None")
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
                **_REASONING_KWARGS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"""User's preferences: {criteria_text}

Products found:
{products_text}

Write the recommendation message."""}
                ],
                max_completion_tokens=2000,
            )
            message = (completion.choices[0].message.content or "").strip()
            if not message:
                # gpt-5-nano occasionally returns empty content; use fallback
                return f"Here are top {domain} recommendations based on your preferences. What would you like to do next?"
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
                **_REASONING_KWARGS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                response_format=RefinementClassification,
            )
            result = completion.choices[0].message.parsed
            if not result:
                logger.warning("Refinement classification returned None")
                return None
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
