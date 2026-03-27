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


def _canonical_brand(value: str) -> Optional[str]:
    """Map raw token/alias to canonical brand if recognized."""
    if not value:
        # Empty/None input cannot map to a brand.
        return None
    # Remove quotes and surrounding whitespace from model/user text.
    raw = value.strip().strip('"\'')
    if not raw:
        # String with only quotes/spaces is effectively empty.
        return None
    # Remove leading/trailing punctuation that frequently appears in extracted tokens,
    # e.g. "HP," -> "HP", "(Dell)" -> "Dell".
    raw = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", raw).strip()
    if not raw:
        return None
    # Strip common negation wrappers that LLM occasionally emits as brand values,
    # e.g. "not ASUS" -> "ASUS".
    raw = re.sub(r'^(?:no|not|exclude|excluded|avoid|without)\s+', '', raw, flags=re.IGNORECASE).strip()
    # Resolve product-family aliases (mac -> Apple, rog -> ASUS, etc.).
    mapped = _BRAND_VALUE_ALIASES.get(raw.lower(), raw)
    # Return canonical brand only if it's in the controlled known-brand set.
    return mapped if mapped in _KNOWN_BRANDS else None


def _parse_excluded_brands(raw_value: Any) -> List[str]:
    """
    Parse/normalize excluded_brands values into canonical known brands only.
    Accepts list/string and drops unknown tokens (e.g. "14", "unknown").
    """
    if raw_value is None:
        # No exclusion payload supplied.
        return []
    if isinstance(raw_value, list):
        # Already list-shaped (from prior normalized state).
        parts = [str(x).strip() for x in raw_value if str(x).strip()]
    else:
        # Parse comma/semicolon/slash-delimited text payloads.
        parts = [p.strip() for p in re.split(r"[,;/|]", str(raw_value)) if p.strip()]
    out: List[str] = []
    for part in parts:
        # Canonicalize each token; unknown/non-brand tokens are dropped.
        c = _canonical_brand(part)
        if c and c not in out:
            # Preserve insertion order while deduplicating.
            out.append(c)
    return out


def _brand_exclusion_from_text(message: str) -> List[str]:
    """
    Lightweight regex exclusion detector used to repair malformed LLM brand outputs.
    """
    # Include indirect phrasing seen in failures ("steer clear of HP", "bad experience with Dell")
    # so we can recover exclusions when LLM returns malformed brand slots.
    pat = re.compile(
        r'(?:no|not|never|anything but|avoid|hate|refuse|skip|exclude|excluded|without|'
        r'steer\s+clear\s+of|bad\s+experience\s+with|terrible\s+experience\s+with)\s+'
        r'([A-Za-z][A-Za-z0-9\- ]{1,30})',
        re.IGNORECASE,
    )
    found: List[str] = []
    for m in pat.finditer(message):
        # Capture group may include grouped patterns: "HP or Acer".
        raw_group = m.group(1).strip()
        parts = re.split(r'\s+(?:or|and|nor)\s+|[,;]\s*', raw_group)
        for p in parts:
            # Keep first lexical token for brand matching robustness.
            token = p.strip().split()[0] if p.strip() else ""
            token = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", token)
            c = _canonical_brand(token)
            if c and c not in found:
                # Ordered dedupe for deterministic behavior.
                found.append(c)
    return found


def _brands_mentioned_in_text(message: str) -> List[str]:
    """
    Extract canonical brands explicitly mentioned in the user message.
    Used to guard against hallucinated LLM brand exclusions.
    """
    text = (message or "").lower()
    found: List[str] = []
    # Check canonical brand names first.
    for brand in sorted(_KNOWN_BRANDS):
        if re.search(rf"(?<!\w){re.escape(brand.lower())}(?!\w)", text):
            if brand not in found:
                found.append(brand)
    # Check common aliases/shorthands (mac -> Apple, rog -> ASUS, etc.).
    for alias, canon in _BRAND_VALUE_ALIASES.items():
        if re.search(rf"(?<!\w){re.escape(alias.lower())}(?!\w)", text):
            if canon in _KNOWN_BRANDS and canon not in found:
                found.append(canon)
    return found


def _filter_exclusions_by_message_mentions(excluded: List[str], message: str) -> List[str]:
    """
    Keep only excluded brands that are explicitly mentioned in user text.
    This removes occasional semantic-extractor hallucinations (e.g., random "HP").
    """
    if not excluded:
        return []
    mentioned = set(_brands_mentioned_in_text(message))
    if not mentioned:
        # No brand evidence in user text -> don't keep any inferred exclusion brands.
        return []
    return [b for b in excluded if b in mentioned]


def _parse_excluded_screen_sizes(raw_value: Any) -> List[float]:
    """
    Parse/normalize excluded screen sizes into validated inch floats.
    Accepts list/string and keeps values in a realistic laptop range.
    """
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        parts = [str(x).strip() for x in raw_value if str(x).strip()]
    else:
        parts = [p.strip() for p in re.split(r"[,;/|]", str(raw_value)) if p.strip()]
    out: List[float] = []
    for part in parts:
        m = re.search(r"\d{2}(?:\.\d+)?", part)
        if not m:
            continue
        val = float(m.group(0))
        if 10.0 <= val <= 21.0 and val not in out:
            out.append(val)
    return out


def _extract_excluded_screen_sizes_from_text(message: str) -> List[float]:
    """
    Detect negated screen-size mentions (e.g., "don't want 14 inch screen")
    and return explicit excluded-size constraints.
    """
    text = message or ""
    pat = re.compile(
        r"(?:no|not|don't\s+want|do\s+not\s+want|avoid|exclude|without|hate)"
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


def _message_has_screen_negation(message: str) -> bool:
    """
    Detect explicit negation around screen/display size constraints.
    This protects against LLM outputs that convert "don't want 14 inch" into
    positive filters like screen_size=14 or min_screen_size=14.
    """
    text = message or ""
    return bool(re.search(
        r"(?:don't\s+want|do\s+not\s+want|no|not|avoid|exclude|without|hate)"
        r".{0,28}(?:\d{2}(?:\.\d)?\s*(?:inch|inches|\"|″)|screen|display)",
        text,
        re.IGNORECASE,
    ))

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

            elif slot_name == "excluded_screen_sizes":
                # Explicit negative screen-size constraints from negated user phrasing.
                excl_sizes = _parse_excluded_screen_sizes(value)
                if excl_sizes:
                    search_filters["excluded_screen_sizes"] = excl_sizes

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
                brands = _parse_excluded_brands(value)
                if brands:
                    search_filters["excluded_brands"] = brands
                    logger.info(f"Excluded brands: {brands}")

            elif slot_name == "os":
                search_filters["os"] = value

            elif slot_name == "product_subtype":
                # Override product_type with the more-specific user-stated subtype.
                # e.g. "laptop_bag" → search for bags, not laptops.
                val_str = str(value).strip().lower()
                if val_str:
                    search_filters["product_subtype"] = val_str

            elif slot_name in ("fuel_type", "condition", "screen_size", "color", "material"):
                search_filters[slot_name] = value

        # Final reconciliation for brand/exclusion semantics.
        # 1) Normalize excluded list to canonical brands only.
        excl = _parse_excluded_brands(search_filters.get("excluded_brands"))
        if excl:
            search_filters["excluded_brands"] = excl
        else:
            search_filters.pop("excluded_brands", None)

        # 2) Convert malformed negated brand values (e.g. "not ASUS") into exclusions.
        if isinstance(search_filters.get("brand"), str):
            raw_brand = search_filters["brand"].strip()
            if re.search(r'^(?:no|not|exclude|excluded|avoid|without)\s+', raw_brand, re.IGNORECASE):
                neg = _canonical_brand(raw_brand)
                search_filters.pop("brand", None)
                if neg:
                    merged = _parse_excluded_brands(search_filters.get("excluded_brands"))
                    if neg not in merged:
                        merged.append(neg)
                    search_filters["excluded_brands"] = merged
            else:
                canon = _canonical_brand(raw_brand)
                if canon:
                    search_filters["brand"] = canon

        # 3) If preferred brand is present, remove it from excluded_brands.
        if isinstance(search_filters.get("brand"), str):
            pref = search_filters["brand"]
            merged = _parse_excluded_brands(search_filters.get("excluded_brands"))
            merged = [b for b in merged if b.lower() != pref.lower()]
            if merged:
                search_filters["excluded_brands"] = merged
            else:
                search_filters.pop("excluded_brands", None)

        # 4) Normalize excluded screen sizes for deterministic downstream filtering.
        excl_sizes = _parse_excluded_screen_sizes(search_filters.get("excluded_screen_sizes"))
        if excl_sizes:
            search_filters["excluded_screen_sizes"] = excl_sizes
        else:
            search_filters.pop("excluded_screen_sizes", None)

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

        # ── "Changed my mind" preference reset ──────────────────────────────────
        # If the user signals a preference change mid-session (same domain), clear
        # soft slot values (brand, use_case) so the new preference fully replaces the
        # old one.  Budget and RAM are kept (user hasn't said they changed those).
        _PREF_RESET_PHRASES = (
            "changed my mind", "change my mind", "actually", "instead show",
            "show me instead", "forget that", "never mind", "nevermind",
            "scratch that", "different brand", "switch to", "go with",
        )
        msg_lower_chk = message.lower()
        if any(p in msg_lower_chk for p in _PREF_RESET_PHRASES) and self.domain:
            # Clear only the soft preferences — keep hard constraints (budget, RAM, screen_size)
            _soft_slots = {"brand", "use_case", "color", "os", "product_subtype"}
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
            _has_spec_signal = bool(re.search(
                r'\$\d|\d+\s*(?:gb|tb|k\b)|under\s+\$|budget|brand|gaming|coding|school|work',
                message, re.IGNORECASE,
            ))
            # Require at least one HARD constraint — a slot that actually narrows search.
            # Soft slots (use_case, os, product_subtype) alone are not enough:
            # "best laptop 2024" → LLM guesses use_case=general/product_subtype=laptop
            # but that doesn't tell us anything actionable. Only price/brand/specs count.
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
            if _msg_words_count <= 5 and not _has_spec_signal and not _hard_criteria:
                extraction_result.wants_recommendations = False
                logger.info(
                    f"Vagueness gate: overrode wants_recommendations=False "
                    f"(words={_msg_words_count}, no spec signals, no criteria extracted)"
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
                       "latop", "labtop", "student", "school", "college", "class",
                       # Screen-size intent usually refers to laptop specs in this app.
                       "screen", "display", "inch", "inches",
                       # Add laptop-brand cues for standalone exclusion phrases like "avoid HP".
                       "hp", "dell", "asus", "lenovo", "acer", "apple", "msi", "razer")
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

            # Hard guardrail: preserve explicit negative screen-size intent even when
            # the LLM returns an empty criteria list for this turn.
            if schema.domain == "laptops" and _message_has_screen_negation(message):
                msg_excl_sizes = _extract_excluded_screen_sizes_from_text(message)
                if msg_excl_sizes:
                    existing_criteria = list(result.criteria or [])
                    has_excl_size_slot = any(c.slot_name == "excluded_screen_sizes" for c in existing_criteria)
                    if not has_excl_size_slot:
                        existing_criteria.append(
                            SlotValue(
                                slot_name="excluded_screen_sizes",
                                value=",".join(str(s) for s in msg_excl_sizes),
                            )
                        )
                        result.criteria = existing_criteria

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
                    "use_case", "excluded_screen_sizes",
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
                    # Repair malformed negated-brand outputs, e.g. "not HP" from LLM.
                    if re.search(r'^(?:no|not|exclude|excluded|avoid|without)\s+', raw_brand, re.IGNORECASE):
                        _neg_canon = _canonical_brand(raw_brand)
                        if _neg_canon:
                            parsed_excl = _parse_excluded_brands(new_filters.get("excluded_brands"))
                            if _neg_canon not in parsed_excl:
                                parsed_excl.append(_neg_canon)
                            new_filters["excluded_brands"] = parsed_excl
                            new_filters.pop("brand", None)
                # Canonicalize excluded_brands and drop hallucinated/non-brand tokens.
                if "excluded_brands" in new_filters:
                    clean_excl = _parse_excluded_brands(new_filters["excluded_brands"])
                    # Guard against malformed LLM outputs that inject unrelated brands.
                    clean_excl = _filter_exclusions_by_message_mentions(clean_excl, message)
                    if clean_excl:
                        new_filters["excluded_brands"] = clean_excl
                    else:
                        new_filters.pop("excluded_brands", None)
                # Canonicalize excluded_screen_sizes into validated numeric inches.
                if "excluded_screen_sizes" in new_filters:
                    clean_excl_sizes = _parse_excluded_screen_sizes(new_filters["excluded_screen_sizes"])
                    if clean_excl_sizes:
                        new_filters["excluded_screen_sizes"] = clean_excl_sizes
                    else:
                        new_filters.pop("excluded_screen_sizes", None)
                # Backstop: if message text clearly indicates exclusion, enforce excluded_brands.
                # This catches "steer clear of HP" when LLM outputs brand="not HP".
                if schema.domain == "laptops":
                    _msg_excl = _brand_exclusion_from_text(message)
                    if _msg_excl:
                        _existing = _parse_excluded_brands(new_filters.get("excluded_brands"))
                        for b in _msg_excl:
                            if b not in _existing:
                                _existing.append(b)
                        new_filters["excluded_brands"] = _existing
                        # If chosen brand conflicts with exclusion semantics, drop it.
                        if isinstance(new_filters.get("brand"), str):
                            _chosen = _canonical_brand(new_filters["brand"])
                            if _chosen and _chosen in _existing:
                                new_filters.pop("brand", None)
                # Negated screen wording should never turn into positive screen filters.
                # Keep this after slot normalization so any alias form is removed.
                if schema.domain == "laptops" and _message_has_screen_negation(message):
                    # Persist explicit negative constraint so filters show user intent.
                    msg_excl_sizes = _extract_excluded_screen_sizes_from_text(message)
                    if msg_excl_sizes:
                        existing_sizes = _parse_excluded_screen_sizes(new_filters.get("excluded_screen_sizes"))
                        for s in msg_excl_sizes:
                            if s not in existing_sizes:
                                existing_sizes.append(s)
                        new_filters["excluded_screen_sizes"] = existing_sizes
                    new_filters.pop("screen_size", None)
                    new_filters.pop("min_screen_size", None)
                    new_filters.pop("max_screen_size", None)
                logger.info(f"Extracted filters (normalised): {new_filters}")
                self.filters.update(new_filters)
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
            if result.criteria and not result.wants_recommendations:
                substantive = [c for c in result.criteria if c.slot_name not in ("os",)]
                msg_words = len(message.split())
                has_question_mark = "?" in message
                # Rhetorical: user listed ≥2 constraints + asked a question like
                # "can any run Final Cut Pro?", "Is that possible?" — treat as req statement.
                is_rhetorical = has_question_mark and len(substantive) >= 2
                if (
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
        # Matches: "no HP", "not HP", "anything but HP", "avoid HP", "hate HP",
        #          "no HP or Acer", "no HP, no Acer"
        _known_brands = [
            "HP", "Acer", "Dell", "Lenovo", "Apple", "ASUS", "Asus",
            "MSI", "Razer", "Samsung", "Microsoft", "LG", "Gigabyte",
            "Framework", "System76", "ROG", "Alienware",
        ]
        _excl_kw_pat = re.compile(
            r'(?:no|not|never|anything but|avoid|hate|refuse|skip|exclude|excluded|without|'
            r'steer\s+clear\s+of|bad\s+experience\s+with|terrible\s+experience\s+with)\s+'
            r'([A-Za-z][A-Za-z0-9\- ]{1,30})',
            re.IGNORECASE
        )
        excl_brands: List[str] = []
        for _m in _excl_kw_pat.finditer(message):
            # Group can be "HP or Acer" or "HP, no Acer" — split on or/and/, to get each brand
            raw_group = _m.group(1).strip()
            parts = re.split(r'\s+(?:or|and)\s+|[,;]\s*', raw_group)
            for part in parts:
                candidate = part.strip().split()[0]  # first word of each part
                candidate_normalized = _canonical_brand(candidate)
                if candidate_normalized:
                    for brand in _known_brands:
                        if brand.lower() == candidate_normalized.lower():
                            if brand not in excl_brands:
                                excl_brands.append(brand)

        # LLM semantic exclusion: catches indirect phrases, bad experiences, sarcasm
        # that the regex above can't handle ("steer clear of HP", "we hate mac", etc.)
        _llm_excl = _extract_excluded_brands_semantic(message)
        for _eb in _llm_excl:
            if _eb not in excl_brands:
                excl_brands.append(_eb)
                logger.info(f"LLM exclusion detected: {_eb}")
        # Guardrail: keep exclusions only when the brand is actually mentioned in text.
        excl_brands = _filter_exclusions_by_message_mentions(excl_brands, message)

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
        # Negative screen-size intent: keep an explicit exclusion list in filters.
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
                    r'(?:no|not|avoid|hate|exclude|without|steer\s+clear\s+of|don.t\s+(?:want|like))\s+' + re.escape(alias),
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
        # Heuristic: if ≥1 substantive criteria extracted, no question mark, and message
        # has ≥4 words — the user is stating requirements, not asking a question.
        # Lowered from ≥2 to ≥1 to avoid penalising evaluation queries that provide
        # a single clear constraint (e.g., "I need a gaming laptop under $1000").
        substantive = [c for c in criteria if c.slot_name not in ("os",)]
        if not wants_recs and len(substantive) >= 1 and "?" not in message and len(message.split()) >= 4:
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
            self.filters.update(new_filters)
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
    _EXTRACT_ONLY_SLOTS = frozenset({"excluded_brands", "os", "product_subtype"})

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
