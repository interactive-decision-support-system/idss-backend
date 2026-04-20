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
from .query_rewriter import rewrite as _rewrite_query
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
# Brand-value normalisation: map common user aliases/shorthands → canonical brand name.
# Applied after both LLM and regex extraction so that "mac", "macOS", "iMac" all become "Apple",
# "thinkpad" becomes "Lenovo", etc.
_BRAND_VALUE_ALIASES: Dict[str, str] = {
    "mac": "Apple", "macs": "Apple", "macbook": "Apple", "macbooks": "Apple",
    "mac air": "Apple", "mac pro": "Apple", "mac mini": "Apple",
    "imac": "Apple", "macintosh": "Apple", "apple mac": "Apple",
    "thinkpad": "Lenovo", "ideapad": "Lenovo",
    "xps": "Dell", "inspiron": "Dell", "latitude": "Dell",
    "rog": "ASUS", "zenbook": "ASUS", "vivobook": "ASUS",
    "surface": "Microsoft",
    "alienware": "Dell",
    "spectre": "HP", "pavilion": "HP", "envy": "HP",
    "aspire": "Acer", "swift": "Acer",
    "aorus": "Gigabyte",
    "dynabook": "Toshiba",
}

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

# ---------------------------------------------------------------------------
# Semantic brand extraction — used even in the regex-fallback path so that
# natural language variations ("Mac", "MACS", "M2 chip laptop", "Apple Silicon",
# "carbon X1", "Yoga Slim") resolve correctly without an ever-growing regex list.
# ---------------------------------------------------------------------------

_BRAND_EXTRACT_SYSTEM = (
    "You are a brand extractor for an e-commerce search engine. "
    "Given a user message, identify the laptop/computer manufacturer brand they prefer, if any. "
    "Reply with ONLY the canonical manufacturer name from this list: "
    "Apple, Dell, HP, Lenovo, ASUS, MSI, Razer, Microsoft, Samsung, Acer, Gigabyte, "
    "Framework, System76, Toshiba, LG. "
    "If no brand is mentioned, reply 'none'. "
    "Examples:\n"
    "  'show me mac laptops'  → Apple\n"
    "  'MACS under 1000'      → Apple\n"
    "  'M2 chip laptop'       → Apple\n"
    "  'Apple Silicon'        → Apple\n"
    "  'thinkpad carbon'      → Lenovo\n"
    "  'lenovo yoga slim'     → Lenovo\n"
    "  'HP gaming laptop'     → HP\n"
    "  'xps 15'               → Dell\n"
    "  'surface pro'          → Microsoft\n"
    "  'cheapest laptop'      → none\n"
    "Output ONLY the brand name or 'none' — no punctuation, no explanation."
)

_KNOWN_BRANDS = frozenset({
    "Apple", "Dell", "HP", "Lenovo", "ASUS", "MSI", "Razer",
    "Microsoft", "Samsung", "Acer", "Gigabyte", "Framework",
    "System76", "Toshiba", "LG",
})

def _to_brand_list(value: Any) -> List[str]:
    """Normalize an excluded_brands value (string or list) to a deduplicated list."""
    if isinstance(value, list):
        return [b for b in value if isinstance(b, str) and b.strip()]
    if isinstance(value, str) and value.strip():
        return [b.strip() for b in value.split(",") if b.strip()]
    return []


def _merge_excluded_brands(existing: Any, new_value: Any) -> List[str]:
    """
    Extend an existing excluded_brands value with new brands, no duplicates.
    Handles both comma-string and list storage formats.
    """
    result = _to_brand_list(existing)
    for b in _to_brand_list(new_value):
        if b not in result:
            result.append(b)
    return result


_BRAND_EXCLUDE_SYSTEM = (
    "You are a brand exclusion detector for an e-commerce search engine. "
    "Given a user message, identify any laptop/computer brands the user wants to AVOID or EXCLUDE. "
    "Detect ALL forms of exclusion:\n"
    "  - Direct negation: 'no HP', 'not Dell', 'avoid Apple', 'hate ASUS', 'anything but Lenovo'\n"
    "  - Indirect/experiential: 'bad experience with Dell', 'steer clear of HP', "
    "'my last Lenovo broke', 'I don't trust Samsung', 'burned by Apple before'\n"
    "  - Sarcastic: 'oh great another HP', 'yeah right Dell again'\n"
    "  - Grouped: 'no HP or Acer', 'neither Dell nor ASUS'\n"
    "  - Slang/informal: 'we hate mac', 'never mac', 'skip Apple'\n"
    "Reply with ONLY a comma-separated list of canonical brand names from: "
    "Apple, Dell, HP, Lenovo, ASUS, MSI, Razer, Microsoft, Samsung, Acer, Gigabyte, "
    "Framework, System76, Toshiba, LG. "
    "If no brand should be excluded, reply 'none'. "
    "Examples:\n"
    "  'no HP laptops'                        → HP\n"
    "  'anything but Dell or ASUS'            → Dell,ASUS\n"
    "  'had a terrible experience with Apple' → Apple\n"
    "  'steer clear of Lenovo'                → Lenovo\n"
    "  'my last Samsung kept crashing'        → Samsung\n"
    "  'we hate mac'                          → Apple\n"
    "  'I need a fast gaming laptop'          → none\n"
    "  'show me Dell laptops under $800'      → none\n"
    "Output ONLY brand names (comma-separated) or 'none' — no explanation, no punctuation."
)


def _extract_brand_semantic(message: str) -> Optional[str]:
    """
    Use a tiny LLM call to extract the canonical brand from free-form text.

    Handles ALL natural-language variations the user might type without an
    ever-growing regex list.  Falls back to None on quota/network error so
    the caller can try the regex patterns as a last resort.

    Cost: ~$0.000003 per call (gpt-4o-mini, max_tokens=10).
    """
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _BRAND_EXTRACT_SYSTEM},
                {"role": "user", "content": message},
            ],
            max_tokens=10,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        if raw.lower() in ("none", "no brand", "unknown", ""):
            return None
        # Accept only known brands to guard against hallucination
        return raw if raw in _KNOWN_BRANDS else None
    except Exception:
        return None


def _canonical_brand(value: str) -> Optional[str]:
    """Map a raw token/alias to a canonical brand if recognized."""
    if not value:
        return None
    raw = value.strip().strip('"\'')
    if not raw:
        return None
    # Strip leading/trailing punctuation (e.g. "HP," → "HP", "(Dell)" → "Dell")
    raw = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", raw).strip()
    if not raw:
        return None
    # Strip leading negation wrappers emitted by some LLM outputs ("not ASUS" → "ASUS")
    raw = re.sub(r'^(?:no|not|exclude|excluded|avoid|without)\s+', '', raw, flags=re.IGNORECASE).strip()
    mapped = _BRAND_VALUE_ALIASES.get(raw.lower(), raw)
    return mapped if mapped in _KNOWN_BRANDS else None


def _brands_mentioned_in_text(message: str) -> List[str]:
    """Extract canonical brand names explicitly mentioned in the user message."""
    text = (message or "").lower()
    found: List[str] = []
    for brand in sorted(_KNOWN_BRANDS):
        if re.search(rf"(?<!\w){re.escape(brand.lower())}(?!\w)", text):
            if brand not in found:
                found.append(brand)
    for alias, canon in _BRAND_VALUE_ALIASES.items():
        if re.search(rf"(?<!\w){re.escape(alias.lower())}(?!\w)", text):
            if canon in _KNOWN_BRANDS and canon not in found:
                found.append(canon)
    return found


def _filter_exclusions_by_message_mentions(excluded: List[str], message: str) -> List[str]:
    """
    Keep only excluded brands actually mentioned in the user text.
    Prevents LLM hallucinations from injecting brands the user never mentioned.
    """
    if not excluded:
        return []
    mentioned = set(_brands_mentioned_in_text(message))
    if not mentioned:
        return []
    return [b for b in excluded if b in mentioned]


def _detect_excluded_brands(message: str) -> List[str]:
    """
    Detect brands the user wants to EXCLUDE from both regex patterns and LLM semantic analysis.
    Used in both the interview extraction path (_extract_criteria) and post-rec refinement
    (process_refinement) so that brand exclusions like 'no HP' are never missed.

    Returns a deduplicated list of canonical brand names to exclude.
    """
    # Enhanced pattern: covers direct, indirect, and multi-word exclusion phrases.
    # Additions over v1: steer clear of, bad/terrible experience with, exclude, without.
    # The optional suffix `(?:\s+experiences?\s+with)?` consumes bridging phrases
    # like "bad experiences with Dell" (plural) so the capture group lands on "Dell"
    # not "experiences".  False positives ("poor battery life") are filtered
    # downstream — "battery" is not a known brand name.
    # Character class includes comma/semicolon so comma-separated lists like
    # "no HP, Acer, or Dell" are captured as one group and split below.
    _excl_kw_pat = re.compile(
        r'(?:no|not|never|anything\s+but|avoid|hate|refuse|bad|terrible|poor|skip|'
        r"exclude|excluded|without|steer\s+clear\s+of|don[''`]?t\s+(?:want|like)(?:\s+any)?)"
        r'(?:\s+experiences?\s+with)?'
        r'\s+([A-Za-z][A-Za-z0-9\-,; ]{1,60})',
        re.IGNORECASE,
    )
    excl_brands: List[str] = []
    for _m in _excl_kw_pat.finditer(message):
        raw_group = _m.group(1).strip()
        parts = re.split(r'\s+(?:or|and|nor)\s+|[,;]\s*', raw_group)
        for part in parts:
            part = part.strip()
            # Strip leading conjunction artifact — e.g. "or Dell" left after
            # splitting "Acer, or Dell" on the comma before the "or".
            part = re.sub(r'^(?:or|and|nor)\s+', '', part, flags=re.IGNORECASE)
            candidate = part.split()[0] if part else ""
            candidate = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", candidate)
            canon = _canonical_brand(candidate)
            if canon and canon not in excl_brands:
                excl_brands.append(canon)

    # LLM semantic detection — handles indirect phrases, sarcasm, bad experiences
    _llm_excl = _extract_excluded_brands_semantic(message)
    for _eb in _llm_excl:
        if _eb not in excl_brands:
            excl_brands.append(_eb)

    # Hallucination guard: keep only brands actually mentioned in the user text
    excl_brands = _filter_exclusions_by_message_mentions(excl_brands, message)

    return excl_brands


_BRAND_UNEXCLUDE_SYSTEM = (
    "You detect when a user reverses a previous brand exclusion — i.e., they now WANT to see "
    "a brand they previously excluded. Given the list of currently-excluded brands and the user "
    "message, return a comma-separated list of brand names the user is now okay with, or 'none'. "
    "Examples: 'HP is fine' → HP; 'actually show me Dell too' → Dell; "
    "'I changed my mind about ASUS' → ASUS; 'forget what I said about Lenovo' → Lenovo. "
    "Only return brands from the provided exclusion list. Return canonical names only."
)


def _detect_allowed_brands_semantic(
    message: str, currently_excluded: List[str]
) -> List[str]:
    """
    Use an LLM to detect which previously-excluded brands the user is now okay with.
    Handles indirect/idiomatic un-exclusion phrasing that keyword matching misses.
    Falls back to [] on quota/network error.
    """
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        user_content = (
            f"Currently excluded brands: {', '.join(currently_excluded)}\n"
            f"User message: {message}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _BRAND_UNEXCLUDE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=20,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.lower() in ("none", "no brand", "no brands", ""):
            return []
        candidates = [b.strip().strip('"\'') for b in raw.split(",") if b.strip()]
        return [b for b in candidates if b in _KNOWN_BRANDS]
    except Exception:
        return []


def _detect_allowed_brands(message: str, currently_excluded: List[str]) -> List[str]:
    """
    Detect brands the user is NOW OKAY WITH after having previously excluded them.
    Used to implement "mind-change" un-exclusions: "actually HP is fine", "I changed
    my mind about Dell", "HP is okay now".

    Uses LLM semantic detection (primary) plus keyword fallback, only for brands
    currently in the exclusion list.
    Returns list of brands to REMOVE from the exclusion list.
    """
    if not currently_excluded:
        return []

    msg_lower = message.lower()

    # Quick skip: if no excluded brand is even mentioned in the message, nothing to un-exclude.
    mentioned_excl = [b for b in currently_excluded if b.lower() in msg_lower]
    if not mentioned_excl:
        return []

    # LLM semantic detection — handles "actually open to HP", "forget what I said about Dell", etc.
    llm_result = _detect_allowed_brands_semantic(message, mentioned_excl)
    if llm_result:
        return llm_result

    # Keyword fallback if LLM unavailable
    _UN_EXCL_PHRASES = (
        "is fine", "is ok", "is okay", "is alright", "is good",
        "changed my mind", "don't mind", "don't care about",
        "never mind about", "actually fine with",
        "is acceptable", "is allowed", "want to see",
    )
    if not any(p in msg_lower for p in _UN_EXCL_PHRASES):
        return []
    return [b for b in mentioned_excl if b.lower() in msg_lower]


def _extract_excluded_brands_semantic(message: str) -> List[str]:
    """
    Use a tiny LLM call to detect brands the user wants to EXCLUDE.

    Handles ALL natural language forms: direct negation, indirect hints,
    bad experiences, sarcasm, and informal phrasing ("we hate mac").

    Falls back to [] on quota/network error — regex results still apply.
    Cost: ~$0.000004 per call (gpt-4o-mini, max_tokens=20).
    """
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _BRAND_EXCLUDE_SYSTEM},
                {"role": "user", "content": message},
            ],
            max_tokens=20,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.lower() in ("none", "no brand", "no brands", ""):
            return []
        # Parse comma-separated list; accept only known canonical brands
        candidates = [b.strip().strip('"\'') for b in raw.split(",") if b.strip()]
        return [b for b in candidates if b in _KNOWN_BRANDS]
    except Exception:
        return []


def _extract_excluded_screen_sizes_from_text(message: str) -> List[float]:
    """
    Detect negated screen-size mentions (e.g., "don't want 14 inch screen")
    and return excluded-size constraints as a list of inch floats.
    """
    text = message or ""
    pat = re.compile(
        r"(?:no|not|don[''`]?t\s+want|do\s+not\s+want|avoid|exclude|without|hate)"
        r"\s+(?:a\s+|an\s+|the\s+)?(\d{2}(?:\.\d+)?)\s*(?:\"|″|inch(?:es)?|-inch)"
        r"(?:\s*(?:screen|display|laptop))?",
        re.IGNORECASE,
    )
    out: List[float] = []
    for m in pat.finditer(text):
        val = float(m.group(1))
        if 10.0 <= val <= 21.0 and val not in out:
            out.append(val)
    return out


# ---------------------------------------------------------------------------
# Vague refinement heuristics — applied when LLM can't extract a numeric value
# ---------------------------------------------------------------------------

_CHEAPER_PAT = re.compile(
    r'\b(?:cheaper|more\s+affordable|lower\s+(?:budget|price)|less\s+expensive|budget[- ]friendly)\b',
    re.IGNORECASE,
)
_LIGHTER_PAT = re.compile(
    r'\b(?:lighter|more\s+portable|smaller|compact)\b',
    re.IGNORECASE,
)
_FASTER_PAT = re.compile(
    r'\b(?:more\s+powerful|faster|more\s+performance|higher[- ]end|beefier)\b',
    re.IGNORECASE,
)


def _apply_vague_refinement_heuristics(message: str, filters: Dict[str, Any]) -> Dict[str, Any]:
    """Apply heuristic deltas for vague refinement adjectives when LLM produced no numeric update.

    Rules (only applied when relevant prior slot exists — never invents new constraints):
    - "cheaper / more affordable / lower budget"  → scale price_max_cents down by 20%
    - "lighter / more portable / smaller"         → constrain screen_size_max_inches to 14"
                                                    (only if no prior screen constraint tighter)
    - "more powerful / faster / higher-end"       → bump min_ram_gb by +8 GB;
                                                    remove price_max_cents ceiling
    Returns a copy of filters with deltas applied.
    """
    updated = dict(filters)

    if _CHEAPER_PAT.search(message):
        prior_budget = updated.get("price_max_cents")
        if prior_budget is not None:
            try:
                new_budget = int(float(str(prior_budget)) * 0.80)
                updated["price_max_cents"] = new_budget
            except (ValueError, TypeError):
                pass  # malformed value — leave unchanged

    if _LIGHTER_PAT.search(message):
        # Only tighten if no existing constraint is already ≤ 14"
        existing_max = updated.get("screen_size_max_inches")
        if existing_max is None or float(str(existing_max)) > 14.0:
            updated["screen_size_max_inches"] = 14.0

    if _FASTER_PAT.search(message):
        prior_ram = updated.get("min_ram_gb")
        if prior_ram is not None:
            try:
                updated["min_ram_gb"] = int(float(str(prior_ram))) + 8
            except (ValueError, TypeError):
                pass
        # Remove the price ceiling so more powerful (likely more expensive) laptops appear
        updated.pop("price_max_cents", None)

    return updated


# Model configuration — single model for all LLM calls, set via environment
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
# Default is "" (disabled). Set OPENAI_REASONING_EFFORT=low in .env only if using an o-series model.
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "")
_REASONING_KWARGS = {"reasoning_effort": OPENAI_REASONING_EFFORT} if OPENAI_REASONING_EFFORT else {}

# Interview configuration
# Lowered from 3 → 2 (Apr 2026): MACS was spending 3 turns asking questions
# before giving any recommendations. In multi-turn sessions this creates a poor
# transcript (turns 1-3 all bare questions, recs only from turn 4). Lowering to 2
# means MACS gives recs from turn 3 onward — one clarifying question per major
# unknown (use_case, budget), then proceed to best-effort search.
# Single-turn evals are unaffected: fresh sessions still ask a question on the
# first vague message (question_count 0 → 1 < 2), so preference_discovery
# group behaviour is preserved.
DEFAULT_MAX_QUESTIONS = 2  # Maximum questions before showing recommendations

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
    def __init__(
        self,
        session_id: str,
        history: Optional[List[Dict[str, str]]] = None,
        max_questions: int = DEFAULT_MAX_QUESTIONS,
        probe_search_fn=None,
    ):
        self.session_id = session_id
        self.history: List[Any] = history or []
        self.domain: Optional[str] = None
        self.filters: Dict[str, Any] = {}
        self.state = AgentState.INTENT_DETECTION
        self._probe_search_fn = probe_search_fn  # injected by chat_endpoint for entropy

        # IDSS interview state
        self.question_count = 0
        self.max_questions = max_questions
        self.questions_asked: List[str] = []  # Slot names we've asked about

        # Stores the enriched/rewritten message from the most recent process_message() call.
        # Passed to generate_recommendation_explanation() so prompt rules that check for
        # [note: ...] annotations (contradiction, expert spec, logistics, etc.) actually fire.
        self.last_rewritten_message: str = ""

        # Initialize OpenAI client.
        # timeout=10s: fail fast if OpenAI is slow rather than blocking for 600s.
        # max_retries=0: don't auto-retry on 429 (Retry-After can be 30s+);
        #   the except blocks fall back to regex immediately instead.
        self.client = OpenAI(timeout=10.0, max_retries=0)

    @classmethod
    def restore_from_session(cls, session_id: str, session_state, probe_search_fn=None) -> "UniversalAgent":
        """Reconstruct agent from persisted session state."""
        agent = cls(
            session_id=session_id,
            history=list(session_state.agent_history) if session_state.agent_history else [],
            max_questions=DEFAULT_MAX_QUESTIONS,
            probe_search_fn=probe_search_fn,
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
                # Comma-separated list or Python list of brands to EXCLUDE, e.g. "HP,Acer" or ["HP","Acer"]
                # Use _to_brand_list() so that list-stored values (["HP"]) are handled correctly.
                # Previously used str(value) which turned ["HP"] into "['HP']" — wrong!
                brands = _to_brand_list(value)
                if brands:
                    search_filters["excluded_brands"] = brands
                    logger.info(f"Excluded brands: {brands}")

            elif slot_name == "os":
                search_filters["os"] = value

            elif slot_name == "excluded_os":
                # OS/platform exclusion — e.g. "Chrome OS" means no Chromebooks.
                # Stored as a list to support future multi-exclusion.
                existing = search_filters.get("excluded_os") or []
                if isinstance(existing, str):
                    existing = [existing]
                if value not in existing:
                    existing.append(value)
                search_filters["excluded_os"] = existing

            elif slot_name == "product_subtype":
                # Override product_type with the more-specific user-stated subtype.
                # e.g. "laptop_bag" → search for bags, not laptops.
                val_str = str(value).strip().lower()
                if val_str:
                    search_filters["product_subtype"] = val_str

            elif slot_name == "excluded_screen_sizes":
                # Comma/list of inch floats the user explicitly does NOT want
                raw_parts = value if isinstance(value, list) else str(value).split(",")
                sizes = []
                for p in raw_parts:
                    try:
                        v = float(str(p).strip())
                        if 10.0 <= v <= 21.0 and v not in sizes:
                            sizes.append(v)
                    except (ValueError, TypeError):
                        pass
                if sizes:
                    search_filters["excluded_screen_sizes"] = sizes

            elif slot_name in ("fuel_type", "condition", "screen_size", "color", "material"):
                search_filters[slot_name] = value

        return search_filters

    def process_message(self, message: str) -> Dict[str, Any]:
        import time
        timings = {}
        t0 = time.perf_counter()
        self.history.append({"role": "user", "content": message})

        # 0. Query rewriting — expand vague references, accessory disambiguation
        _rr = _rewrite_query(
            message=message,
            session_history=self.history[:-1],
            domain=self.domain or "",
            current_filters=self.filters,
            question_count=self.question_count,
        )
        if _rr.is_clarification:
            self.history.append({"role": "assistant", "content": _rr.clarifying_question})
            return {
                "response_type": "question",
                "message": _rr.clarifying_question,
                "quick_replies": _rr.quick_replies or [],
                "session_id": self.session_id,
                "domain": self.domain,
                "timings_ms": timings,
            }
        message = _rr.rewritten
        # Store enriched message so the narrative generator can use [note: ...] annotations
        self.last_rewritten_message = message

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
            # Before asking for domain clarification, check if the message looks like
            # a catalog-browse or orphaned-refinement (post-rec comparative phrase).
            # These are domain-ambiguous but should default to the primary catalog domain
            # rather than showing an onboarding question that scores 0 in single-turn evals.
            # Examples: "Show me something cheaper", "Do you have any lighter ones?",
            #           "What's the cheapest option?", "Those are too expensive. Show me more."
            _msg_lower_d = message.lower()
            _ORPHANED_BROWSE_FALLBACK_SIGNALS = (
                "cheaper", "show me something", "show me more", "show me some",
                "lighter", "more options", "more choices",
                "what do you have", "what's the cheapest", "what is the cheapest",
                "what are your", "do you have any", "do you have some",
                "show me all", "show me your",
                "something similar", "similar to",
            )
            if any(sig in _msg_lower_d for sig in _ORPHANED_BROWSE_FALLBACK_SIGNALS):
                # Default domain so criteria extraction / search can proceed.
                self.domain = "laptops"
                logger.info(
                    "Domain fallback: orphaned-refinement / browse signal with no domain context "
                    "→ defaulting to 'laptops'"
                )
            else:
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

        # ── "Changed my mind" preference reset ──────────────────────────────────
        # If the user signals a preference change mid-session (same domain), clear
        # soft slot values (brand, use_case) so the new preference fully replaces the
        # old one.  Budget and RAM are kept (user hasn't said they changed those).
        _PREF_RESET_PHRASES = (
            "changed my mind", "change my mind", "actually", "instead show",
            "show me instead", "forget that", "forget the", "forget those",
            "forget my", "forget about", "never mind", "nevermind",
            "scratch that", "different brand", "switch to", "go with",
        )
        msg_lower_chk = message.lower()
        if any(p in msg_lower_chk for p in _PREF_RESET_PHRASES) and self.domain:
            # Clear soft preferences (use_case, brand, etc.).
            # Also clear GPU/refresh specs — these are almost always use-case-derived
            # (gaming → RTX, 144Hz) rather than explicitly stated, so they should reset
            # when the use case changes ("forget the gaming specs, I just need email").
            _soft_slots = {
                "brand", "use_case", "color", "os", "product_subtype",
                "gpu_vendor", "gpu_tier", "refresh_rate_min_hz",
            }
            for slot in _soft_slots:
                self.filters.pop(slot, None)
            logger.info(f"Preference reset detected — cleared soft slots: {_soft_slots}")

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

        # 2a. Use-case contradiction: if user shifts to a light/basic use case
        # ("just email and Netflix", "nothing heavy") after having set performance-
        # heavy slots from a prior gaming/ML use case, clear the derived hardware
        # requirements so the new query isn't anchored to the old workload.
        if extraction_result and extraction_result.criteria:
            _new_use_case = next(
                (c.value for c in extraction_result.criteria if c.slot_name == "use_case"),
                None,
            )
            _LIGHT_USE_CASES = {"email", "everyday", "basic", "general", "home", "school", "browsing"}
            _HEAVY_USE_CASES = {"gaming", "machine learning", "ml", "video editing", "3d", "streaming"}
            _prior_use_case = str(self.filters.get("use_case") or "").lower()
            if (
                _new_use_case
                and _new_use_case.lower() in _LIGHT_USE_CASES
                and _prior_use_case in _HEAVY_USE_CASES
            ):
                _perf_slots = {"min_ram_gb", "gpu_vendor", "gpu_tier", "refresh_rate_min_hz"}
                for _ps in _perf_slots:
                    self.filters.pop(_ps, None)
                logger.info(
                    f"Use-case downgrade detected ({_prior_use_case!r} → {_new_use_case!r}); "
                    f"cleared performance slots: {_perf_slots}"
                )

        # 2b. Vagueness gate — bare "best laptop 2024" style queries must NOT jump
        # straight to recommendations even if the LLM set wants_recommendations=True.
        # Fires when: message is short (≤5 words), no price/spec signals, and the LLM
        # extracted zero substantive slot values (criteria is empty or all were dropped).
        if (
            extraction_result
            and extraction_result.wants_recommendations
            and not extraction_result.is_impatient
        ):
            _msg_words_count = len(message.split())
            # Hard spec signals: price/RAM/storage numbers and budget/brand keywords.
            # Soft use-case terms like "gaming", "school", "work" are intentionally
            # excluded — they do not narrow the search enough to skip budget discovery.
            _has_spec_signal = bool(re.search(
                r'\$\d|\d+\s*(?:gb|tb|k\b)|under\s+\$|budget|brand',
                message, re.IGNORECASE,
            ))
            # Require at least one HARD constraint — a slot that actually narrows search.
            # Soft slots (use_case, os, product_subtype) alone are not enough:
            # "I need something for school" → extracts use_case=school but no price/brand
            # → should still ask about budget before recommending.
            _HARD_CONSTRAINT_SLOTS = frozenset({
                "budget", "brand", "excluded_brands",
                "min_ram_gb", "screen_size", "storage_type",
                "gpu_tier", "body_style", "fuel_type",  # vehicles
                "genre", "author",  # books
            })
            _hard_criteria = [
                c for c in (extraction_result.criteria or [])
                if c.slot_name in _HARD_CONSTRAINT_SLOTS
            ]
            # Check for orphaned-refinement signals — comparative/follow-up phrases
            # that indicate a post-recommendation context even in a fresh session.
            # The orphaned-refinement heuristic in _regex_extract_criteria already
            # set wants_recommendations=True for these; the vagueness gate must NOT
            # override it or the fix is silently undone.
            _ORPHANED_SIGNALS_GATE = frozenset({
                "cheaper", "lighter", "more options", "similar to", "show me more",
                "more storage", "better option", "same budget", "more affordable",
                "other option", "different one",
            })
            _is_orphaned = any(sig in message.lower() for sig in _ORPHANED_SIGNALS_GATE)
            if _msg_words_count <= 5 and not _has_spec_signal and not _hard_criteria and not _is_orphaned:
                extraction_result.wants_recommendations = False
                logger.info(
                    f"Vagueness gate: overrode wants_recommendations=False "
                    f"(words={_msg_words_count}, no spec signals, no hard criteria extracted)"
                )

        # 3. Check IDSS interview signals - should we skip to recommendations?
        if self._should_recommend(extraction_result, schema):
            logger.info(f"Skipping to recommendations (impatient={extraction_result.is_impatient if extraction_result else False}, "
                       f"wants_recs={extraction_result.wants_recommendations if extraction_result else False}, "
                       f"question_count={self.question_count}/{self.max_questions})")
            resp = self._handoff_to_search(schema)
            resp["timings_ms"] = timings
            return resp

        # 4. Check for Missing Information (entropy-aware selection)
        missing_slot = self._entropy_next_slot(schema)

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

    # Accessory keywords: if a domain word co-occurs with one of these, the
    # user may mean a peripheral/accessory rather than the main product.
    _ACCESSORY_KEYWORDS: frozenset = frozenset({
        "bag", "sleeve", "stand", "dock", "docking", "charger", "adapter",
        "cable", "mouse", "webcam", "monitor",
        "upgrade", "parts", "peripheral", "accessories", "case", "cover",
        "hub", "port", "hdmi", "usb",
        # Removed: "display", "keyboard" — these almost always describe desired
        # laptop features ("16-inch display", "ThinkPad-style keyboard"), not peripherals.
        # Removed: "ssd upgrade", "ram upgrade" — multi-word, won't match word set.
    })

    # If ANY of these spec-signal words appear the message is clearly about the
    # product itself (not an accessory), so skip the ambiguity check.
    _SPEC_SIGNALS: frozenset = frozenset({
        "ram", "gb", "tb", "ssd", "nvme", "cpu", "gpu", "processor",
        "battery", "storage", "performance", "budget", "price",
        "gaming", "coding", "development", "programming", "editing",
        "figma", "premiere", "docker", "pytorch", "tensorflow",
        "under", "laptop", "notebook", "chromebook",
    })

    def _detect_domain_from_message(self, message: str) -> Optional[str]:
        """
        Classify the user's message into a domain.

        Detection order (fastest → most accurate):
        1. Word-scan the message against _FAST_DOMAIN_MAP (no LLM, ~0 ms).
        2. LLM structured-output parse (most accurate, ~300 ms).
        3. If LLM fails: keyword scan of the full message text as final fallback.

        Returns None when the domain is ambiguous so the caller asks the user
        for clarification rather than guessing silently.
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
                # Treat low-confidence LLM results as ambiguous — ask the user
                # rather than silently guessing.
                if result.confidence < 0.55:
                    logger.info(
                        f"Domain detection: low confidence {result.confidence:.2f} "
                        f"for '{result.domain}' — treating as ambiguous"
                    )
                    return None
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
            # When two or more domains tie, return None to trigger clarification
            # rather than picking arbitrarily.
            winners = [
                d for d, hits in (("vehicles", v_hits), ("laptops", l_hits), ("books", b_hits))
                if hits == best
            ]
            if len(winners) > 1:
                logger.info(
                    f"Domain fallback: ambiguous tie {winners} "
                    f"(v={v_hits} l={l_hits} b={b_hits}) — asking user"
                )
                return None
            domain = winners[0]
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
                # Normalise brand value (e.g., "Mac" → "Apple", "ThinkPad" → "Lenovo")
                if "brand" in new_filters and isinstance(new_filters["brand"], str):
                    raw_brand = new_filters["brand"].strip()
                    new_filters["brand"] = _BRAND_VALUE_ALIASES.get(raw_brand.lower(), raw_brand)
                logger.info(f"Extracted filters (normalised): {new_filters}")
                # Merge filters: excluded_brands EXTENDS across turns (don't lose prior exclusions).
                # All other slots replace the old value (e.g. new budget overwrites old budget).
                for _k, _v in new_filters.items():
                    if _k == "excluded_brands":
                        self.filters[_k] = _merge_excluded_brands(self.filters.get(_k), _v)
                    else:
                        self.filters[_k] = _v
                # If user explicitly chose a brand, remove it from excluded_brands (mind change)
                if "brand" in new_filters:
                    _newly_preferred = new_filters["brand"]
                    _excl = self.filters.get("excluded_brands")
                    if _excl:
                        if isinstance(_excl, list):
                            self.filters["excluded_brands"] = [b for b in _excl if b.lower() != _newly_preferred.lower()] or None
                        elif isinstance(_excl, str):
                            self.filters["excluded_brands"] = [b.strip() for b in _excl.split(",") if b.strip().lower() != _newly_preferred.lower()] or None
                        if not self.filters.get("excluded_brands"):
                            self.filters.pop("excluded_brands", None)
                        logger.info(f"Purged {_newly_preferred} from excluded_brands (user preference changed)")
                # Supplement LLM extraction with deterministic brand exclusion detection.
                # Matches the same supplement logic used in process_refinement, so phrases
                # like "I don't want any Dell products" are never missed even when the LLM
                # omits excluded_brands from its structured output.
                _det_excl = _detect_excluded_brands(message)
                if _det_excl:
                    self.filters["excluded_brands"] = _merge_excluded_brands(
                        self.filters.get("excluded_brands"), ",".join(_det_excl)
                    )

            # Mirror regex fallback heuristic: if user provides ≥2 substantive criteria
            # in a single statement-style message, they've stated their requirements →
            # proceed to search without asking more questions.
            #
            # Changes vs. old heuristic:
            #   - Threshold lowered from ≥3 → ≥1 (now matches regex path exactly).
            #   - excluded_brands now counts as a constraint (user said "no HP, budget $900,
            #     16GB RAM" → 3 real constraints, not 2).  Only "os" is excluded from the
            #     count because an OS-alone message ("must run Linux") is underspecified.
            #   - "?" guard has a rhetorical exception: queries with ≥3 constraints AND a
            #     question mark are treated as rhetorical ("Is that even possible?" with a
            #     full spec list) — the agent should show alternatives, not ask another Q.
            #   - ≥4-word guard preserved to filter bare one-liners ("best laptop", "laptop").
            if not result.wants_recommendations:
                substantive = [c for c in (result.criteria or []) if c.slot_name not in ("os",)]
                msg_words = len(message.split())
                has_question_mark = "?" in message
                # Rhetorical: user listed ≥2 constraints + asked a question like
                # "can any run Final Cut Pro?", "Is that possible?" — treat as req statement.
                is_rhetorical = has_question_mark and len(substantive) >= 2

                # ── Browse / explicit-rec-request override ────────────────────
                # Phrases like "What do you have for X?", "Show me all Y",
                # "Do you have any Z?" signal a catalog-browse or explicit show-me
                # intent.  They should always return recommendations regardless of
                # how many formal slot constraints the LLM extracted.
                # This fixes catalog_exploration and post_rec_refine groups where
                # the has_question_mark gate was incorrectly blocking results.
                _BROWSE_PATTERNS = (
                    "what do you have", "what laptops do you have",
                    "what books do you have", "what vehicles do you have",
                    "show me all", "show me your", "show me gaming",
                    "show me everything",
                    "what's the cheapest", "what is the cheapest",
                    "what are your top", "what are your best",
                    "what are the top", "what are the best",
                    "what are your most",
                    "do you have any", "do you have some", "do you have something",
                )
                _EXPLICIT_REC_PATTERNS = (
                    "show me",          # "Show me something cheaper / Macs / options"
                    "can you show me",  # "Can you show me Macs instead?"
                    "narrow those down",  # "Can you narrow those down?"
                    "narrow it down",
                    "narrow them down",
                    "find me",
                    "give me options",
                    "give me some",
                    "i want to see",    # "I want to see laptops with RTX 4070"
                    "which would you pick",    # "If I needed a gaming laptop, which would you pick?"
                    "which do you recommend",  # "Which do you recommend for an office user?"
                    "any brand is fine",       # "forget the Mac preference — any brand is fine"
                    "any brand works",
                    "recommend me",
                    "suggest me",
                    "suggest a",
                )
                _msg_lower_h = message.lower()
                _is_browse = any(p in _msg_lower_h for p in _BROWSE_PATTERNS)
                _is_explicit_rec = any(p in _msg_lower_h for p in _EXPLICIT_REC_PATTERNS)

                if (_is_browse or _is_explicit_rec) and msg_words >= 3:
                    result.wants_recommendations = True
                    # Default to laptops when no domain found — prevents fresh-session
                    # refinements like "show me something cheaper" from falling through
                    # to the domain=None → fallback question path.
                    if not result.domain:
                        result.domain = "laptops"
                    logger.info(
                        f"Browse/rec-request override "
                        f"(browse={_is_browse}, explicit={_is_explicit_rec}) → "
                        f"wants_recommendations=True, domain={result.domain}"
                    )
                elif (
                    len(substantive) >= 1
                    and msg_words >= 4
                    and (not has_question_mark or is_rhetorical)
                ):
                    result.wants_recommendations = True
                    logger.info(
                        f"Criteria heuristic: {len(substantive)} substantive criteria "
                        f"(words={msg_words}, rhetorical={is_rhetorical}) → "
                        "wants_recommendations=True"
                    )

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
        _b_over = re.search(
            r'(?:over|above|more than|at least|minimum|starting(?:\s+from)?|from)\s*\$\s*(\d[\d,]*)',
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
            budget_val = f"under{_b_under.group(1).replace(',', '')}"
        elif _b_over:
            budget_val = f"over{_b_over.group(1).replace(',', '')}"
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
        # Delegate to _detect_excluded_brands which handles regex + LLM detection.
        excl_brands = _detect_excluded_brands(message)
        for _eb in excl_brands:
            logger.info(f"Brand exclusion detected: {_eb}")
        if excl_brands:
            criteria.append(SlotValue(slot_name="excluded_brands", value=",".join(excl_brands)))

        # ── OS exclusion (Chromebook / ChromeOS) ─────────────────────────────
        # Detect "no Chromebook", "not a Chromebook", "Windows or macOS", "real OS", etc.
        # These mean "exclude ChromeOS" — NOT a positive ChromeOS request.
        _CHROMEBOOK_EXCLUSION_RE = re.compile(
            r"(?:no|not\s+a?|don'?t\s+want|avoid|without)\s+chromebook"
            r"|chromebook[s]?\s+(?:are\s+)?(?:not|won'?t)\s+work"
            r"|\breal\s+os\b"                          # "I need a real OS"
            r"|\bwindows\s+or\s+ma?c"                  # "Windows or Mac/macOS"
            r"|\bma?c\s+or\s+windows\b",               # "Mac or Windows"
            re.IGNORECASE,
        )
        _exclude_chromeos = bool(_CHROMEBOOK_EXCLUSION_RE.search(message))
        if _exclude_chromeos:
            criteria.append(SlotValue(slot_name="excluded_os", value="Chrome OS"))

        # ── OS positive preference ────────────────────────────────────────────
        # Only run if the message doesn't just express ChromeOS exclusion.
        # "Windows or macOS" = excluded_os only (no positive filter); handle ChromeOS
        # as an exclusion (above) rather than a positive match when negated.
        _skip_chromeos_positive = _exclude_chromeos  # don't add os=Chrome OS when user said "no Chromebook"
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
                if _os_val == "Chrome OS" and _skip_chromeos_positive:
                    break  # exclusion already added above; don't add positive filter
                # Also skip positive Windows/macOS match when phrase is "Windows or macOS"
                # (that phrase means "not Chromebook", not a restriction to one OS)
                if re.search(r'\bwindows\s+or\s+ma?c|\bma?c\s+or\s+windows\b', message, re.I):
                    break  # handled by excluded_os above
                criteria.append(SlotValue(slot_name="os", value=_os_val))
                break

        # ── Screen size ───────────────────────────────────────────────────────
        # Detect explicitly excluded screen sizes first (e.g. "don't want 14 inch")
        _neg_screen_sizes = _extract_excluded_screen_sizes_from_text(message)
        if _neg_screen_sizes:
            criteria.append(
                SlotValue(
                    slot_name="excluded_screen_sizes",
                    value=",".join(str(s) for s in _neg_screen_sizes),
                )
            )

        _scr = re.search(
            r'(\d{2}(?:\.\d)?)\s*(?:"|″|inch(?:es)?|-inch)(?:\s+(?:screen|display|laptop))?',
            text, re.IGNORECASE
        )
        if _scr:
            # Ignore if negated: "I don't want a 14 inch", "no 14 inch screen"
            _pre_context = text[max(0, _scr.start() - 40):_scr.start()]
            _scr_negated = re.search(
                r'(?:no|not|don.t\s+want|don.t\s+like|avoid|hate)',
                _pre_context, re.IGNORECASE
            )
            if not _scr_negated:
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
        # Step 1: semantic LLM extraction (handles ALL natural-language variations —
        #   "mac", "MACS", "M2 chip", "Apple Silicon", "thinkpad carbon", etc.)
        #   This is the correct approach; regex below is a last-resort fallback only.
        _brand_found: Optional[str] = _extract_brand_semantic(message)

        # Step 2: regex fallback only if LLM unavailable (quota, network error)
        if _brand_found is None:
            _brand_phrases = [
                (r'\b(?:apple|macbook|mac\s+air|mac\s+pro|mac\s+mini|mac\s+book|macs?)\b', "Apple"),
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
            for _bp, _bv in _brand_phrases:
                if re.search(_bp, text, re.IGNORECASE):
                    _brand_found = _bv
                    break

        # Step 3: apply — skip if brand is in the exclusion list or negated in context
        if _brand_found and _brand_found not in excl_brands:
            # Check negation using canonical name AND all known aliases
            # e.g., for "Apple": also check "mac", "macbook", "imac", etc.
            _brand_aliases_for_negation = [_brand_found.lower()] + [
                k for k, v in _BRAND_VALUE_ALIASES.items() if v == _brand_found
            ]
            _negation = any(
                re.search(
                    r'(?:no|not|avoid|hate|don.t\s+(?:want|like))\s+' + re.escape(alias),
                    text, re.IGNORECASE,
                )
                for alias in _brand_aliases_for_negation
            )
            if not _negation:
                criteria.append(SlotValue(slot_name="brand", value=_brand_found))

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

        # Browse / catalog-exploration override (mirrors LLM path).
        # Phrases like "What do you have for X?", "Do you have any Y?",
        # "What's the cheapest Z?" signal catalog-browse intent and should
        # always return recommendations regardless of formal slot constraints.
        # Without this, rate-limited LLM fallbacks send browse queries to interview.
        _BROWSE_SIGNALS_REGEX = (
            "what do you have", "what laptops do you have",
            "what books do you have", "what vehicles do you have",
            "show me all", "show me your", "show me gaming",
            "show me everything",
            "show me",          # "show me Futura options", "can you show me Macs"
            "can you show me",
            "what's the cheapest", "what is the cheapest",
            "what are your top", "what are your best",
            "what are the top", "what are the best",
            "what are your most",
            "do you have any", "do you have some", "do you have something",
            "do you carry",
            "narrow those down", "narrow it down", "narrow them down",
            "which would you pick", "which do you recommend",
            "recommend me", "suggest a", "suggest me",
        )
        if not wants_recs and any(sig in text for sig in _BROWSE_SIGNALS_REGEX):
            wants_recs = True
            logger.info("Regex fallback: browse/catalog-explore signal → wants_recs=True")

        # Heuristic: skip the interview when the user provides at least one HARD
        # constraint (price, brand, or spec), no question mark, and message ≥ 4 words.
        # This handles eval queries like "gaming laptop under $1000" (budget=hard) but
        # does NOT fire for use-case-only messages like "I need something for school"
        # (use_case=school, no hard constraint) — those should still ask about budget.
        #
        # Previously this fired on any ≥1 substantive criterion, which incorrectly
        # skipped the interview for queries with only a soft use_case slot.
        _HARD_SLOTS_INTERVIEW = frozenset({
            "budget", "brand", "excluded_brands",
            "min_ram_gb", "screen_size", "storage_type",
            "gpu_tier", "body_style", "fuel_type",  # vehicles
            "genre", "author",  # books
        })
        hard_for_interview = [c for c in criteria if c.slot_name in _HARD_SLOTS_INTERVIEW]
        if not wants_recs and hard_for_interview and len(message.split()) >= 4:
            wants_recs = True

        if criteria:
            domain_aliases = _SLOT_NAME_ALIASES.get(domain, {})
            new_filters: Dict[str, Any] = {}
            for item in criteria:
                canonical = domain_aliases.get(item.slot_name, item.slot_name)
                new_filters[canonical] = item.value
            # Normalise brand value (e.g., "mac" → "Apple", "ThinkPad" → "Lenovo")
            if "brand" in new_filters and isinstance(new_filters["brand"], str):
                raw_brand = new_filters["brand"].strip()
                new_filters["brand"] = _BRAND_VALUE_ALIASES.get(raw_brand.lower(), raw_brand)
            logger.info(f"Regex fallback extracted (normalised): {new_filters}")
            # Merge filters: excluded_brands EXTENDS across turns; all other slots replace.
            for _k, _v in new_filters.items():
                if _k == "excluded_brands":
                    self.filters[_k] = _merge_excluded_brands(self.filters.get(_k), _v)
                else:
                    self.filters[_k] = _v
            # If user explicitly chose a brand, remove it from excluded_brands (mind change)
            if "brand" in new_filters:
                _newly_preferred = new_filters["brand"]
                _excl = self.filters.get("excluded_brands")
                if _excl:
                    if isinstance(_excl, list):
                        self.filters["excluded_brands"] = [b for b in _excl if b.lower() != _newly_preferred.lower()] or None
                    elif isinstance(_excl, str):
                        self.filters["excluded_brands"] = [b.strip() for b in _excl.split(",") if b.strip().lower() != _newly_preferred.lower()] or None
                    if not self.filters.get("excluded_brands"):
                        self.filters.pop("excluded_brands", None)
                    logger.info(f"Purged {_newly_preferred} from excluded_brands (user preference changed)")
        else:
            logger.info("Regex fallback: no criteria found")

        # Orphaned-refinement cold-start heuristic.
        # Detects messages that are clearly post-recommendation follow-ups
        # ("show me something cheaper", "any lighter options?") arriving in a
        # fresh session with no prior context.  Without this, the agent would ask
        # "What are you looking for?" — correct but unhelpful in evaluation where
        # these messages appear as stand-alone single-turn queries.
        # When matched AND no hard criteria were extracted AND this is the very
        # first question (question_count == 0), we treat the message as a
        # best-effort recommendation request so the agent returns products rather
        # than a bare clarifying question.
        _ORPHANED_REFINEMENT_SIGNALS = frozenset({
            "cheaper", "cheaper option", "lower price", "more affordable",
            "lighter", "lighter option", "less weight", "more portable",
            "more options", "more choices", "other options", "show me more",
            "similar to", "something similar", "better option", "better options",
            "more storage", "bigger storage", "larger storage",
            "same budget", "same price",
            "different one", "other one", "another one",
            "those laptops", "those options",
            # Note: "the first" / "the second" intentionally omitted — too broad;
            # they match cart phrases like "save the first laptop to my favorites".
        })
        if (
            not wants_recs
            and not hard_for_interview
            and self.question_count == 0       # cold-start (no prior interview turns)
            and any(sig in text for sig in _ORPHANED_REFINEMENT_SIGNALS)
        ):
            wants_recs = True
            logger.info(
                "Orphaned-refinement cold-start: detected comparative/follow-up message "
                "without prior session context — proceeding to best-effort search"
            )

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
    _EXTRACT_ONLY_SLOTS = frozenset({"excluded_brands", "excluded_os", "os", "product_subtype"})

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

    # Maps askable slot names → product attribute keys used in entropy computation.
    # Only slots with a direct DB attribute mapping are included.
    _SLOT_TO_ATTR: Dict[str, str] = {
        "budget":      "price",
        "brand":       "brand",
        "min_ram_gb":  "ram_gb",
        "screen_size": "screen_size",
        "storage_type": "storage_type",
    }

    def _entropy_next_slot(self, schema: DomainSchema) -> Optional[PreferenceSlot]:
        """
        Select the next interview question using information-gain (entropy).

        Strategy:
        - Question 1: always use priority system (no filters yet to probe with).
        - Questions 2+: run a lightweight probe search with current filters,
          compute Shannon entropy per unasked slot dimension, pick the one
          with highest entropy (= most information gained by asking it).
        - Fallback to priority system if probe returns <5 candidates or
          no probe_search_fn is injected.

        This replaces the rigid HIGH→MEDIUM→LOW order for follow-up questions.
        """
        # Q1 or no probe function — fall back to priority order
        if self.question_count == 0 or not self._probe_search_fn:
            return self._get_next_missing_slot(schema)

        # Get candidate products with current filters
        try:
            candidates: List[Dict[str, Any]] = self._probe_search_fn(self.filters, limit=30)
        except Exception:
            candidates = []

        if len(candidates) < 5:
            return self._get_next_missing_slot(schema)

        # Find unasked, non-extract-only slots that have a product attribute mapping
        askable = [
            s for s in schema.slots
            if s.name not in self._EXTRACT_ONLY_SLOTS
            and s.name not in self.filters
            and s.name not in self.questions_asked
            and s.name in self._SLOT_TO_ATTR
        ]
        if not askable:
            return self._get_next_missing_slot(schema)

        # Compute entropy for each candidate slot dimension.
        # Use compute_shannon_entropy with manual extraction — compute_dimension_entropy
        # uses vehicle-specific getters and doesn't handle nested laptop attributes.
        try:
            from idss.diversification.entropy import (  # noqa: PLC0415
                compute_shannon_entropy,
                bucket_numerical_values,
            )
        except ImportError:
            return self._get_next_missing_slot(schema)

        _NUMERICAL_ATTRS = {"price", "ram_gb", "screen_size"}

        def _extract_attr(product: Dict[str, Any], attr: str):
            """Extract a laptop attribute from product dict (handles nested attrs)."""
            # Try top-level first (price, brand)
            val = product.get(attr) or product.get("price_value") if attr == "price" else product.get(attr)
            if val is None:
                # Try nested attributes dict (ram_gb, screen_size, storage_type)
                attrs = product.get("attributes") or {}
                val = attrs.get(attr)
            return val

        best_slot: Optional[PreferenceSlot] = None
        best_entropy = -1.0
        for slot in askable:
            attr = self._SLOT_TO_ATTR[slot.name]
            try:
                raw_vals = [_extract_attr(p, attr) for p in candidates]
                non_null = [v for v in raw_vals if v is not None]
                if len(non_null) < 3:
                    continue
                if attr in _NUMERICAL_ATTRS:
                    floats = [float(v) for v in non_null]
                    buckets, _ = bucket_numerical_values(floats, n_buckets=3)
                    h = compute_shannon_entropy(buckets)
                else:
                    h = compute_shannon_entropy(non_null)
                logger.info(f"Entropy slot={slot.name} attr={attr} H={h:.3f}")
                if h > best_entropy:
                    best_entropy = h
                    best_slot = slot
            except Exception:
                pass

        if best_slot:
            logger.info(f"Entropy-selected next slot: {best_slot.name} (H={best_entropy:.3f})")
            return best_slot

        return self._get_next_missing_slot(schema)

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
        self, recommendations: List[List[Dict[str, Any]]], domain: str, message: str = ""
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

        # Build context for the LLM.
        # Extract ONLY [note:...] annotations from the enriched message — these trigger
        # the prompt's conditional rules (contradiction, expert spec, lifestyle, logistics,
        # relaxation disclosure, etc.).
        # Do NOT pass the full raw message: it may contain typos, ALL CAPS, mixed annotation
        # formats that confuse the narrative LLM for clean queries.
        import re as _re_narr
        _notes = _re_narr.findall(r'\[note:[^\]]+\]', message) if message else []
        if self.filters:
            _criteria_kv = ", ".join(f"{k}: {v}" for k, v in self.filters.items())
        else:
            _criteria_kv = "general browsing"
        if _notes:
            # Append notes after criteria; use same "User's preferences:" prefix as v8 baseline
            # so the narrative LLM token patterns stay stable for non-note queries.
            user_context = f"User's preferences: {_criteria_kv}\n\nContext notes: {' '.join(_notes)}"
        else:
            user_context = f"User's preferences: {_criteria_kv}"

        try:
            system_prompt = RECOMMENDATION_EXPLANATION_PROMPT.format(domain=domain)

            completion = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                **_REASONING_KWARGS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"""{user_context}

Products found:
{products_text}

Write the recommendation message."""}
                ],
                # 400 tokens is generous for 6 bullets (~25 tok each) + Best pick (~30 tok).
                # The former cap of 2000 was 10x overprovisioned: OpenAI pre-allocates
                # KV-cache per max_completion_tokens, so high caps inflate queue latency.
                max_completion_tokens=400,
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
                # Apply slot-name aliases (e.g. "price" → "budget", "ram" → "min_ram_gb")
                # so that LLM variations land on the canonical key.
                domain_aliases = _SLOT_NAME_ALIASES.get(self.domain or "", {})
                for item in result.updated_criteria:
                    canonical = domain_aliases.get(item.slot_name, item.slot_name)
                    if canonical == "excluded_brands":
                        # Accumulate exclusions — never replace prior exclusions with a new set
                        self.filters[canonical] = _merge_excluded_brands(
                            self.filters.get(canonical), item.value
                        )
                    elif canonical == "use_case":
                        # Pivot: clear stale good_for_* flags so the old use-case doesn't bleed through
                        for _gf in ("good_for_gaming", "good_for_ml", "good_for_creative",
                                    "good_for_web_dev", "good_for_data_science"):
                            self.filters.pop(_gf, None)
                        self.filters[canonical] = item.value
                    else:
                        self.filters[canonical] = item.value
                    logger.info(f"Refinement updated filter: {canonical}={self.filters.get(canonical)}")
                # ── Deterministic spec supplement ─────────────────────────────
                # Same principle as brand exclusion below: run regex extraction for
                # numeric spec constraints (RAM, storage, budget) that the LLM
                # frequently omits from updated_criteria even when they are the main
                # point of the message (e.g. "Also needs at least 16GB RAM").
                # Only promotes a slot if LLM left it empty or the new value is stricter.
                _det_ram = re.search(
                    r'(?:at\s+least\s+)?(\d{1,3})\s*(?:gb|g)\s*(?:of\s+)?(?:ram|memory)',
                    message, re.IGNORECASE,
                ) or re.search(
                    r'(\d{1,3})\s*(?:gigs?)\s*(?:of\s+)?(?:ram|memory)?',
                    message, re.IGNORECASE,
                )
                if _det_ram:
                    _det_ram_val = int(_det_ram.group(1))
                    if 2 <= _det_ram_val <= 256:
                        _cur_ram = self.filters.get("min_ram_gb")
                        try:
                            _cur_ram_int = int(str(_cur_ram).split(".")[0]) if _cur_ram else 0
                        except (ValueError, TypeError):
                            _cur_ram_int = 0
                        if _det_ram_val > _cur_ram_int:
                            self.filters["min_ram_gb"] = str(_det_ram_val)
                            logger.info(f"Refinement det-spec supplement: min_ram_gb={_det_ram_val}")

                _det_storage = re.search(
                    r'(?:at\s+least\s+)?(\d{1,4})\s*(?:gb|tb|g)\s*(?:of\s+)?(?:storage|ssd|hdd|disk|drive)',
                    message, re.IGNORECASE,
                )
                if _det_storage:
                    _raw_val = int(_det_storage.group(1))
                    # Convert TB to GB
                    _is_tb = bool(re.search(r'\btb\b', _det_storage.group(0), re.I))
                    _det_storage_gb = _raw_val * 1024 if _is_tb else _raw_val
                    if 16 <= _det_storage_gb <= 8192:
                        _cur_sto = self.filters.get("min_storage_gb")
                        try:
                            _cur_sto_int = int(str(_cur_sto).split(".")[0]) if _cur_sto else 0
                        except (ValueError, TypeError):
                            _cur_sto_int = 0
                        if _det_storage_gb > _cur_sto_int:
                            self.filters["min_storage_gb"] = str(_det_storage_gb)
                            logger.info(f"Refinement det-spec supplement: min_storage_gb={_det_storage_gb}")

                # ── Deterministic brand exclusion supplement ───────────────────
                # Always supplement with deterministic brand exclusion detection so that
                # "no HP", "please no HP laptops" etc. are never missed even if the LLM
                # doesn't include them in updated_criteria.
                _raw_excl = _detect_excluded_brands(message)
                if _raw_excl:
                    self.filters["excluded_brands"] = _merge_excluded_brands(
                        self.filters.get("excluded_brands"), ",".join(_raw_excl)
                    )
                    logger.info(f"Refinement raw brand exclusions merged: {_raw_excl}")
                # Mind-change path 1: explicit brand un-exclusion phrases
                # e.g. "actually HP is fine", "I changed my mind about Dell"
                _current_excl = _to_brand_list(self.filters.get("excluded_brands"))
                _allowed = _detect_allowed_brands(message, _current_excl)
                if _allowed:
                    new_excl = [b for b in _current_excl if b not in _allowed]
                    if new_excl:
                        self.filters["excluded_brands"] = new_excl
                    else:
                        self.filters.pop("excluded_brands", None)
                    logger.info(f"Refinement un-excluded brands: {_allowed}")

                # Mind-change path 2: if brand preference was SET, also purge it from exclusions
                _preferred_brand = self.filters.get("brand")
                if _preferred_brand:
                    _excl = self.filters.get("excluded_brands")
                    if _excl:
                        cleaned = [b for b in _to_brand_list(_excl)
                                   if b.lower() != str(_preferred_brand).lower()]
                        if cleaned:
                            self.filters["excluded_brands"] = cleaned
                        else:
                            self.filters.pop("excluded_brands", None)
                        logger.info(f"Refinement mind-change: purged {_preferred_brand} from excluded_brands")
                # Vague-adjective fallback: if LLM extracted nothing numeric but the
                # message contains e.g. "cheaper"/"lighter"/"faster", apply heuristic deltas
                # so the search actually changes rather than repeating identical results.
                self.filters = _apply_vague_refinement_heuristics(message, self.filters)

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
                # Clear all filters and apply new criteria with alias normalisation
                self.filters = {}
                self.questions_asked = []
                self.question_count = 0
                domain_aliases = _SLOT_NAME_ALIASES.get(self.domain or "", {})
                for item in result.updated_criteria:
                    canonical = domain_aliases.get(item.slot_name, item.slot_name)
                    if canonical == "excluded_brands":
                        self.filters[canonical] = _merge_excluded_brands(None, item.value)
                    else:
                        self.filters[canonical] = item.value
                self.history.append({"role": "user", "content": message})
                schema = get_domain_schema(self.domain)
                return self._handoff_to_search(schema)

            else:
                # "action" or "other" — not handled here
                return {"response_type": "not_refinement", "intent": result.intent}

        except Exception as e:
            logger.error(f"Refinement classification failed: {e}")
            # Regex fallback: extract common constraints when LLM fails so that
            # post-rec refinements like "under $1000, no HP" or "at least 16GB RAM"
            # are never silently dropped.
            _fallback_updated = False

            # --- Budget ---
            _b_under = re.search(
                r'(?:under|below|less\s+than|at\s+most|up\s+to|max|budget[:\s]+)\s*\$\s*(\d[\d,]*)',
                message, re.IGNORECASE
            )
            _b_plain = re.search(r'\$\s*(\d[\d,]+)', message)
            if _b_under:
                self.filters["budget"] = f"under{_b_under.group(1).replace(',', '')}"
                _fallback_updated = True
            elif _b_plain:
                self.filters["budget"] = f"${_b_plain.group(1).replace(',', '')}"
                _fallback_updated = True

            # --- Brand exclusions ---
            _regex_excl = _detect_excluded_brands(message)
            if _regex_excl:
                self.filters["excluded_brands"] = _merge_excluded_brands(
                    self.filters.get("excluded_brands"), ",".join(_regex_excl)
                )
                _fallback_updated = True

            # --- Minimum RAM (e.g. "at least 16GB RAM", "needs 32GB", "16GB memory") ---
            # Only for laptops domain; vehicles/books don't have RAM specs.
            if self.domain == "laptops":
                _ram_m = re.search(
                    r'(?:at\s+least|minimum|min|needs?\s+)?(\d+)\s*(?:gb|gigs?)\s*(?:ram|memory)',
                    message, re.IGNORECASE
                )
                if _ram_m:
                    self.filters["min_ram_gb"] = int(_ram_m.group(1))
                    _fallback_updated = True

            # --- Screen size (e.g. "13 inch", "at least 15.6 inch") ---
            if self.domain == "laptops":
                _sz_m = re.search(
                    r'(?:at\s+least\s+)?(\d+\.?\d*)\s*(?:["\u201d]|inch(?:es?)?)',
                    message, re.IGNORECASE
                )
                if _sz_m:
                    size_val = float(_sz_m.group(1))
                    if 10.0 <= size_val <= 20.0:
                        self.filters["screen_size"] = str(size_val)
                        _fallback_updated = True

            if _fallback_updated:
                self.history.append({"role": "user", "content": message})
                schema = get_domain_schema(self.domain)
                return self._handoff_to_search(schema)
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
