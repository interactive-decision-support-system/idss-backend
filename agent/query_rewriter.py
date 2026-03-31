"""
query_rewriter.py — Disambiguates and expands user queries before slot extraction.

Responsibilities:
1. Accessory disambiguation (moved from universal_agent.py)
2. Context-aware expansion: fills vague references using known session filters
3. Common-sense enrichment: "for my son" → annotates [use_case: school]

Called at the top of UniversalAgent.process_message() before domain detection
and slot extraction.  Returns either a rewritten query string or a clarifying
question to show the user (is_clarification=True).
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RewriteResult:
    rewritten: str                                  # expanded / clarified query (may equal original)
    clarifying_question: Optional[str] = None       # if set, show this to user instead of proceeding
    quick_replies: Optional[List[str]] = field(default_factory=list)
    is_clarification: bool = False


# ---------------------------------------------------------------------------
# Keyword sets (mirrored from former universal_agent.py inline block)
# ---------------------------------------------------------------------------

_ACCESSORY_KEYWORDS: frozenset = frozenset({
    "bag", "sleeve", "stand", "dock", "docking", "charger", "adapter",
    "cable", "mouse", "webcam", "hub", "port", "hdmi", "usb",
    "upgrade", "parts", "peripheral", "accessories", "case", "cover",
})

_SPEC_SIGNALS: frozenset = frozenset({
    "ram", "gb", "tb", "ssd", "nvme", "cpu", "gpu", "processor",
    "battery", "storage", "performance", "budget", "price",
    "gaming", "coding", "development", "programming", "editing",
    "under", "laptop", "notebook", "chromebook",
})

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rewrite(
    message: str,
    session_history: List[Dict[str, Any]],
    domain: str,
    current_filters: Dict[str, Any],
    question_count: int = 0,
) -> RewriteResult:
    """
    Main entry point.  Returns a RewriteResult with the (possibly expanded)
    query or a clarifying question to ask the user.

    Args:
        message:         Raw user message.
        session_history: Prior conversation turns (dicts with 'role'/'content').
        domain:          Active domain ('laptops', 'vehicles', 'books', or '').
        current_filters: Slot values extracted so far (e.g. {'budget': 1200}).
        question_count:  How many questions the agent has already asked.
    """
    msg_lower = message.lower().strip()
    msg_words = set(re.sub(r"[^a-z0-9 ]", " ", msg_lower).split())

    # 0. Hard typo check — physically impossible specs (catch before any LLM call)
    # "1TB RAM" / "2TB of memory" — consumer laptops max out at 64-128GB RAM
    _ram_typo = re.search(
        r'\b(\d+)\s*tb\s*(?:of\s+)?(?:ram|memory)\b',
        msg_lower,
    )
    if _ram_typo:
        tb_val = int(_ram_typo.group(1))
        clarify = (
            f"Just to check — did you mean **{tb_val}TB SSD storage** rather than RAM? "
            f"Consumer laptops top out around 64GB RAM, so {tb_val}TB RAM isn't available. "
            f"I can search for laptops with {tb_val}TB SSD storage if that's what you meant!"
        )
        return RewriteResult(
            rewritten=message,
            clarifying_question=clarify,
            quick_replies=[f"{tb_val}TB SSD storage", "Smaller SSD (256GB–1TB)", "Different spec"],
            is_clarification=True,
        )

    # 1. Accessory / subtype ambiguity check
    accessory_hit = msg_words & _ACCESSORY_KEYWORDS
    spec_hit = msg_words & _SPEC_SIGNALS
    domain_label = {"laptops": "laptop", "vehicles": "vehicle", "books": "book"}.get(domain, domain)

    # 1b. Bare vague query check — "best laptop 2024", "good laptop cheap", "laptop"
    # These have no real constraints (no budget, brand, use case, spec).
    # Return a clarifying question instead of letting the LLM guess.
    _VAGUE_QUERY_PATTERNS = [
        # "best <product> <year>" or "best <product>"
        r'^best\s+\w+(\s+\d{4})?$',
        # "good <product>" or "good <product> cheap/under"
        r'^good\s+\w+(\s+(cheap|affordable))?$',
        # Single word or two-word with only generic modifier
        r'^(great|cheap|decent|budget|affordable|reliable|fast)\s+\w+$',
    ]
    # "laptop", "notebook", "chromebook" are domain nouns, not real spec signals —
    # allow them in vague queries (e.g. "best laptop 2024" should be caught as vague).
    _DOMAIN_NOUNS = frozenset({"laptop", "notebook", "chromebook", "computer", "vehicle", "car", "book"})
    _real_spec_hit = spec_hit - _DOMAIN_NOUNS
    _is_bare_vague = (
        len(msg_words) <= 5
        and not _real_spec_hit
        and not current_filters  # no prior context
        and question_count == 0   # first turn
        and any(re.match(p, msg_lower.strip()) for p in _VAGUE_QUERY_PATTERNS)
    )
    if _is_bare_vague:
        # domain_label may be empty if domain hasn't been detected yet (first turn).
        # Derive label from message keywords as fallback.
        _device = domain_label or (
            "laptop" if any(w in msg_words for w in ("laptop", "notebook", "chromebook")) else
            "vehicle" if any(w in msg_words for w in ("car", "truck", "vehicle")) else
            "book" if "book" in msg_words else "device"
        )
        return RewriteResult(
            rewritten=message,
            clarifying_question=(
                f"Happy to help you find a {_device}! To give you useful recommendations, "
                f"what will you mainly use it for — work, school, gaming, creative projects? "
                f"And what's your budget?"
            ),
            quick_replies=["Work/Business", "School/Study", "Gaming", "Creative Work"],
            is_clarification=True,
        )

    if (accessory_hit
            and question_count == 0
            and not spec_hit
            and len(msg_words) < 20
            and domain_label):
        example = next(iter(accessory_hit))
        clarify = (
            f"Are you looking for a **{domain_label}** itself, or a "
            f"**{domain_label} accessory** (like a {example})?"
        )
        return RewriteResult(
            rewritten=message,
            clarifying_question=clarify,
            quick_replies=[f"The {domain_label} itself", f"A {domain_label} accessory"],
            is_clarification=True,
        )

    # 2. Context-aware expansion: fill vague references from known filters
    expanded = _expand_with_context(message, msg_lower, current_filters)

    # 3. Common-sense enrichment
    expanded = _commonsense_enrich(expanded, msg_lower)

    return RewriteResult(rewritten=expanded)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _expand_with_context(
    message: str,
    msg_lower: str,
    filters: Dict[str, Any],
) -> str:
    """
    Expand vague comparative references using slot values already known.

    Examples:
        "show me cheaper ones" + {price_max_cents: 120000}
            → "show me cheaper ones [under $1200]"
        "different brand" + {brand: "Dell"}
            → "different brand [not Dell]"
    """
    expanded = message

    # "cheaper / more affordable" → append current budget cap
    if re.search(r"\b(cheaper|less expensive|more affordable)\b", msg_lower):
        budget = filters.get("price_max_cents") or filters.get("budget")
        if budget:
            try:
                raw = int(budget)
                price = raw // 100 if raw > 10_000 else raw   # cents → dollars if large
                expanded += f" [under ${price}]"
            except (TypeError, ValueError):
                pass

    # "different / another brand" → append the brand to avoid
    if re.search(r"\b(different brand|another brand|not that brand)\b", msg_lower):
        brand = filters.get("brand")
        if brand:
            expanded += f" [not {brand}]"

    return expanded


def _commonsense_enrich(message: str, msg_lower: str) -> str:
    """
    Annotate implicit intent signals so the downstream LLM extractor
    can pick them up even without explicit phrasing.

    These annotations are enclosed in [] and will be parsed by the
    criteria extractor just like any other slot hint.
    """
    enriched = message

    # "for my son / daughter / kid / child / student" → school use case
    # Also catches "my kid is going to college", "starting college", etc.
    if re.search(
        r"\b(son|daughter|kid|child|nephew|niece|student)\b",
        msg_lower,
    ) or re.search(r"\b(going to|starting|for)\s+(college|university|school)\b", msg_lower):
        enriched += " [use_case: school]"

    # "for work / office / business" (only if not already explicit)
    if (re.search(r"\bfor\s+(work|office|business)\b", msg_lower)
            and "use_case" not in msg_lower):
        enriched += " [use_case: business]"

    # Gaming titles — casual (low-mid spec)
    if re.search(r"\b(minecraft|roblox|fortnite|league of legends|valorant|csgo|cs2)\b", msg_lower):
        enriched += " [use_case: gaming] [note: casual/esports gaming; mid-range GPU sufficient]"

    # Gaming titles — high-end
    if re.search(r"\b(cyberpunk|elden ring|call of duty|warzone|battlefield|baldur.s gate)\b", msg_lower):
        enriched += " [use_case: gaming] [note: AAA gaming; dedicated GPU recommended]"

    # FAQ: Final Cut Pro → Mac-only (inject brand + use_case + os so rhetorical-? exception fires)
    if re.search(r"\bfinal\s+cut\s+pro\b", msg_lower):
        enriched += (
            " [brand: Apple] [use_case: video_editing] [product_subtype: laptop]"
            " [note: Final Cut Pro is Mac-only; only Apple laptops run it natively]"
        )

    # FAQ: Microsoft Office → works on Windows and Mac (inject use_case so ≥1 criterion)
    if re.search(r"\b(microsoft\s+)?office\b", msg_lower) and "use_case" not in msg_lower:
        enriched += (
            " [use_case: business]"
            " [note: Microsoft Office works on Windows and Mac; any modern laptop handles it well]"
        )

    # FAQ: multi-monitor / dual monitor / 3 monitors → inject use_case + product_subtype so threshold met
    if re.search(r"\b(\d+|two|dual|multiple|multi)\s*(-\s*)?(external\s+)?(monitor|screen|display)s?\b", msg_lower):
        enriched += (
            " [use_case: work] [product_subtype: laptop]"
            " [note: multi-monitor setup — briefly answer in 1 sentence that a USB-C/Thunderbolt dock"
            " can drive 3 monitors from most modern laptops. Then recommend specific laptops and note"
            " their port options (Thunderbolt, HDMI). Do not skip the product list.]"
        )

    # Expert/enthusiast spec signals (PCIe gen, DDR5 speed, MUX switch, Wi-Fi 7, etc.)
    # The DB can't filter by these directly → acknowledge in response and route to top-tier
    if re.search(
        r"\b(pcie\s*[45]\.?0|ddr5[-\s]\d{4}|mux\s+switch|wi[-\s]?fi\s*[67]|"
        r"thunderbolt\s*[45]|oled|mini[-\s]?led|ecc\s+ram|ecc\s+memory|"
        r"i[79]-\d{4,5}h|ryzen\s+[79]\s+\d{4}|r[79]-\d{4})\b",
        msg_lower,
    ):
        enriched += (
            " [note: expert query with enthusiast-level specs; our catalog does not filter"
            " by PCIe gen, DDR5 speed rating, or Wi-Fi version directly."
            " Recommend top-tier performance laptops and briefly acknowledge the specific"
            " spec cannot be confirmed from catalog data.]"
        )

    # Logistics / shipping constraints (APO/FPO/military, urgency deadlines)
    _is_apo = bool(re.search(
        r"\b(apo|fpo|dpo|military\s+(?:address|mail)|army\s+post|fleet\s+post)\b", msg_lower
    ))
    _is_urgent = bool(re.search(
        r"\b(need\s+it\s+(today|tomorrow|asap|urgently|by\s+monday|this\s+week)|"
        r"internship\s+starts|starts\s+(monday|tomorrow)|ships?\s+today|"
        r"2[-\s]?day|prime\s+shipping|arriving\s+today)\b",
        msg_lower,
    ))
    if _is_apo and _is_urgent:
        # Merge into a single note to avoid conflicting "open with" directives in the narrative
        enriched += (
            " [note: user has a military APO/FPO address AND an urgent deadline."
            " Open with one sentence: 'APO/FPO shipping varies — Amazon typically supports it;"
            " verify at checkout and prioritize Prime-eligible in-stock options for fast delivery.'"
            " Then give product recommendations as normal.]"
        )
    elif _is_apo:
        enriched += (
            " [note: user has a military APO/FPO shipping address;"
            " acknowledge this upfront — not all retailers ship to APO/FPO."
            " Amazon and direct manufacturer sites typically do."
            " Focus on widely available options; mention to verify shipping at checkout.]"
        )
    elif _is_urgent:
        enriched += (
            " [note: user has an urgent timeline;"
            " prioritize in-stock items with fast shipping (Amazon Prime / 2-day)."
            " Mention to check stock and shipping speed before buying.]"
        )

    # Budget shock — user expressing surprise at laptop prices
    if re.search(
        r"\b(why\s+is\s+everything\s+so\s+expensive|sticker\s+shock|"
        r"chromebook\s+was\s+\$?\d+|was\s+only\s+\$?\d+|"
        r"(now|they)\s+(want|charge)\s+\$?\d+\s+(for|just)|"
        r"can.t\s+believe\s+(the\s+)?price|so\s+overpriced)\b",
        msg_lower,
    ):
        enriched += (
            " [note: budget shock — user is surprised by laptop prices compared to a"
            " cheap device they previously owned. Empathize, then show the most"
            " affordable Windows/Chromebook alternatives in the $200-$500 range.]"
        )

    # Business travel / travel durability
    if re.search(
        r"\b(travel\s+(a\s+lot|for\s+work)|always\s+in\s+airports?|"
        r"flight\s+to|long\s+flight|hotels?|bad\s+wifi|"
        r"on\s+the\s+road|frequent\s+flyer)\b",
        msg_lower,
    ) and "use_case" not in msg_lower:
        enriched += (
            " [use_case: work]"
            " [note: travel use case — still recommend specific laptop products by name."
            " In each bullet emphasize battery life (10h+ preferred) and weight (<3 lbs)."
            " Best pick should call out the longest-lasting / lightest option.]"
        )

    # Facebook Marketplace / Craigslist / used device risk
    _marketplace_hit = re.search(
        r"\b(facebook\s+marketplace|craigslist|ebay|used\s+laptop|second.?hand|"
        r"refurbished\s+deal|seems\s+too\s+good|too\s+good\s+to\s+be\s+true)\b",
        msg_lower,
    )
    _price_legit = _marketplace_hit and \
        re.search(r"\$(3\d{2}|4\d{2}|5\d{2})", msg_lower) and \
        re.search(r"\b(rtx|gtx|gaming)\b", msg_lower)
    if _price_legit:
        # Specific: gaming laptop at suspiciously low price — address legitimacy directly
        enriched += (
            " [note: price legitimacy question — an RTX/gaming laptop for $300-$500 is"
            " almost certainly stolen, damaged, or misrepresented. State clearly:"
            " new RTX 4060 laptops start at $800-$900. This price is a red flag."
            " Then show the cheapest legitimate new equivalent.]"
        )
    elif _marketplace_hit:
        enriched += (
            " [note: marketplace risk — user is comparing a suspiciously cheap"
            " marketplace listing to buying new. Briefly warn about marketplace risk"
            " (stolen goods, no warranty, no returns), then show the cheapest new equivalent.]"
        )

    # Frustrated / venting user — two detection paths:
    # 1. Specific strong words (TERRIBLE, AWFUL, etc.)
    # 2. High all-caps ratio: ≥40% of alpha words are CAPS, and message is long enough
    #    to rule out abbreviations (e.g., "USB SSD NVMe RTX" — all tech abbreviations).
    _already_frustrated = False
    if re.search(
        r"\b(TERRIBLE|AWFUL|HORRIBLE|USELESS|GARBAGE|PIECE\s+OF\s+JUNK|"
        r"MY\s+LAST\s+[A-Z]+\s+DIED|HAD\s+IT\s+WITH)\b",
        message,  # check original message for caps
    ):
        _already_frustrated = True
        enriched += (
            " [note: frustrated user venting about a bad experience."
            " Briefly acknowledge their frustration, then focus on reliability"
            " and the specific problem they mentioned (battery, speed, etc.).]"
        )
    if not _already_frustrated:
        _cap_words = re.findall(r'\b[A-Z]{2,}\b', message)
        _all_alpha_words = re.findall(r'\b[a-zA-Z]{2,}\b', message)
        # Cap words that are tech abbreviations don't count as "shouting"
        _TECH_ABBREVS = frozenset({"USB", "SSD", "NVMe", "RAM", "CPU", "GPU", "RTX", "GTX",
                                    "HDMI", "LED", "LCD", "OLED", "DDR", "PCIe", "WiFi",
                                    "APO", "FPO", "LTE", "AC", "DC", "OS", "TDP", "GHz"})
        _shouting_words = [w for w in _cap_words if w not in _TECH_ABBREVS]
        if (len(_all_alpha_words) >= 5
                and len(_shouting_words) >= 3
                and len(_shouting_words) / max(len(_all_alpha_words), 1) >= 0.35):
            enriched += (
                " [note: frustrated user — message is partially in ALL CAPS indicating strong frustration."
                " Open with 1 short empathetic sentence (e.g., 'Sorry to hear that!') before recommendations.]"
            )

    # Cloud / storage FAQ ("does cloud mean I don't need storage?")
    if re.search(
        r"\b(cloud\s+storage|everything\s+to\s+(the\s+)?cloud|moving\s+to\s+cloud|"
        r"does\s+that\s+mean\s+i\s+don.?t\s+need\s+storage|"
        r"do\s+i\s+need\s+storage|no\s+storage\s+needed)\b",
        msg_lower,
    ):
        enriched += (
            " [note: cloud storage FAQ — user thinks cloud storage replaces local storage."
            " Clarify: local storage (SSD) is still needed for the OS, apps, and offline files."
            " Cloud just syncs files; it doesn't replace the drive.]"
        )

    # Power user / many tabs / heavy multitasking (non-technical description)
    if re.search(
        r"\b(power\s+user|40\s+(?:chrome\s+)?tabs?|tons\s+of\s+tabs|"
        r"dozens\s+of\s+tabs|lots\s+of\s+tabs|heavy\s+multitasking)\b",
        msg_lower,
    ) and "ram" not in msg_lower and "gb" not in msg_lower:
        enriched += (
            " [note: power user — user needs 16GB+ RAM for heavy multitasking."
            " Recommend specific mid-to-high range laptops with ≥16GB RAM."
            " DO NOT explain what RAM is — just pick the right laptops and name them.]"
        )

    # Contradictory requirements: fanless/silent + high-end GPU (physically impossible)
    if re.search(r"\b(fanless|passive\s+cooling|silent\s+fan|0\s*db|0db|no\s+fan)\b", msg_lower) and \
       re.search(r"\b(rtx\s*[34]\d{3}|gtx\s*\d{3,4}|gaming\s+laptop|high.end\s+gaming)\b", msg_lower):
        enriched += (
            " [note: contradictory requirements — no gaming laptop is truly fanless; RTX/GTX"
            " GPUs require active cooling. Open with one sentence acknowledging this trade-off,"
            " then STILL recommend the best available high-performance gaming laptops by name."
            " Do not skip the product recommendations.]"
        )

    # Contradictory: impossible specs (e.g. 20h battery + RTX 4090 + ultralight + cheap)
    if re.search(r"\b(20\s*h(our)?s?\s+battery|all\s+day\s+battery)\b", msg_lower) and \
       re.search(r"\b(rtx\s*4090|rtx\s*4080|rtx\s*3080|rtx\s*3090)\b", msg_lower):
        enriched += (
            " [note: contradictory requirements — RTX 4080/4090 laptops consume 100-175W"
            " under load; 20h battery life is not achievable with these GPUs."
            " Acknowledge the trade-off and show the best battery life available in"
            " high-performance gaming laptops (~6-8h real-world).]"
        )

    # Video editing / creative pro apps that need dedicated GPU
    if re.search(
        r"\b(davinci\s+resolve|premiere\s+pro|after\s+effects|final\s+cut|"
        r"video\s+edit(ing)?|4k\s+edit|color\s+grad(ing)?|motion\s+graphic)\b",
        msg_lower,
    ) and "brand: Apple" not in enriched:  # Final Cut already handled above
        enriched += (
            " [use_case: video_editing]"
            " [note: video editing (especially DaVinci Resolve/Premiere) requires a"
            " dedicated GPU (NVIDIA RTX preferred) for hardware acceleration."
            " In each bullet, note whether the product has a discrete GPU."
            " Flag any integrated-graphics-only options as insufficient for 4K editing.]"
        )

    # CPU comparison questions (e.g. "i7 vs Ryzen 7")
    if re.search(
        r"\b(i[579]-\d{4,5}h?\s+(vs?\.?|or)\s+ryzen|"
        r"ryzen\s+[579]\s+\d{4}\s+(vs?\.?|or)\s+i[579]|"
        r"intel\s+(vs?\.?|or)\s+amd|amd\s+(vs?\.?|or)\s+intel)\b",
        msg_lower,
    ):
        enriched += (
            " [note: CPU comparison question — briefly state which CPU wins for the"
            " user's stated workload (single-thread: Intel edge; multi-thread: AMD edge;"
            " battery: depends on TDP). Then recommend laptops with the better-suited CPU.]"
        )

    # Non-technical / lifestyle user detection.
    # Fires when the user describes needs in plain life terms and uses zero spec language.
    # Signals: daily activities (netflix, zoom, homework), broken device, basic needs.
    # Guard: must NOT have any real spec words (GB, RAM, CPU, etc.) or it's an expert user.
    _lifestyle_signals = re.search(
        r"\b(netflix|youtube|grandma|grandpa|grandparents?|homework|"
        r"doesnt?\s+freeze|freeze|freezing|super\s+slow|just\s+(watch|use|browse)|"
        r"video\s+call|zoom\s+calls?|facetime|skype|"
        r"basic\s+(stuff|things|tasks?)|"
        r"(old|last|my|her|his)\s+\w+\s+(broke|died|broken|stopped working)|"
        r"don.t\s+know\s+(?:what|anything|much)\s+about|"
        r"what\s+(are\s+)?specs|idk\s+what\s+specs)\b",
        msg_lower,
    )
    _tech_signals = re.search(
        r"\b(ram|gb|tb|cpu|gpu|rtx|gtx|nvme|ssd|processor|cores?|ghz|vram|"
        r"display\s+\d|hz|refresh|thunderbolt|pcie|ddr\d|tdp|watt|lbs?\.?|kg)\b",
        msg_lower,
    )
    if _lifestyle_signals and not _tech_signals:
        enriched += (
            " [note: lifestyle/non-technical user — no spec knowledge assumed."
            " Use plain, warm language. Recommend 1-2 clear top picks with simple reasons"
            " ('fast', 'reliable', 'great battery', 'won\\'t slow down or freeze')."
            " Skip spec lists and abbreviations entirely. Lead with what the user cares about.]"
        )

    # Uncertain brand mentions with "?" or "or something" — still extract the brand
    # e.g. "thinkpad? or something" should still be treated as brand=ThinkPad
    if re.search(r"\b(thinkpad|macbook|xps|zenbook|spectre|pavilion|inspiron|aspire)\b", msg_lower):
        brand_map = {
            "thinkpad": "Lenovo ThinkPad",
            "macbook": "Apple MacBook",
            "xps": "Dell XPS",
            "zenbook": "ASUS ZenBook",
            "spectre": "HP Spectre",
            "pavilion": "HP Pavilion",
            "inspiron": "Dell Inspiron",
            "aspire": "Acer Aspire",
        }
        for keyword, brand_name in brand_map.items():
            if keyword in msg_lower:
                enriched += f" [brand_interest: {brand_name}]"
                break

    # Domain switch: user pivoting from laptops to tablet/iPad/Surface/Chromebook
    if re.search(
        r"\b(ipad(\s+pro)?|surface\s+(pro|go|laptop)|tablet|2-in-1|two.in.one)\b",
        msg_lower,
    ) and re.search(r"\b(forget|instead|switch|rather|actually|no\s+more\s+laptop)\b", msg_lower):
        enriched += (
            " [note: user is pivoting from laptop to tablet/2-in-1 (iPad, Surface Pro)."
            " Open with 1 sentence: 'We only carry laptops, but here are the lightest and"
            " most portable options that could work.' Then still recommend specific lightweight"
            " laptops — focus on weight (<3 lbs), thin profile, and touch/2-in-1 designs if available."
            " Do NOT skip the product recommendations.]"
        )

    # -----------------------------------------------------------------------
    # Task 4: Additional empathy signals — confusion / overwhelm / uncertainty
    # These fire even without strong frustration — mild confusion still deserves
    # a warm, reassuring response, not just a clinical spec question.
    # -----------------------------------------------------------------------
    if re.search(
        r"\b(overwhelmed|so\s+many\s+options|don.?t\s+know\s+where\s+to\s+start|"
        r"confused|hard\s+to\s+choose|can.?t\s+decide|hard\s+to\s+pick|"
        r"just\s+help\s+me\s+(choose|decide|pick)|help\s+me\s+choose|"
        r"too\s+many\s+(options|choices)|analysis\s+paralysis|"
        r"any\s+recommendation|not\s+sure\s+what\s+to\s+(get|buy|pick))\b",
        msg_lower,
    ):
        enriched += (
            " [note: user is confused or overwhelmed by choices."
            " Lead with 1 short reassuring sentence (e.g., 'No worries — I\\'ll help narrow it down!')."
            " Then ask ONE focused question or give ONE clear top recommendation."
            " Avoid listing many options at once — be decisive and warm.]"
        )

    # -----------------------------------------------------------------------
    # Task 6: Brand contradiction detection
    # Catches impossible or conflicting brand/OS requirements before search.
    # -----------------------------------------------------------------------

    # "Dell Mac" / "Mac but Dell" — impossible brand combo
    if re.search(r"\b(dell|hp|lenovo|asus|acer|samsung|msi|razer)\b", msg_lower) and \
       re.search(r"\b(mac\b|macbook|apple|macos|mac\s*os)\b", msg_lower) and \
       not re.search(r"\b(vs\.?|versus|compared?\s+to|or\s+the|difference\s+between)\b", msg_lower):
        enriched += (
            " [note: contradictory requirements — the user asked for a non-Apple brand"
            " AND macOS/Mac in the same request (e.g. 'Dell Mac'). macOS only runs"
            " on Apple hardware. Open with exactly 1 sentence clarifying this:"
            " 'Mac/macOS runs exclusively on Apple hardware — Dell, HP, etc. run Windows."
            " I\\'ll show you the best options in each direction.' Then show BOTH"
            " a MacBook option AND a Windows option from the named brand,"
            " letting the user choose. Do NOT skip the product recommendations.]"
        )

    # "Chromebook for gaming" / "gaming Chromebook"
    if re.search(r"\bchromebook\b", msg_lower) and \
       re.search(r"\b(gaming|rtx|gtx|game|steam|fortnite|minecraft|valorant|aaa)\b", msg_lower):
        enriched += (
            " [note: contradictory requirements — Chromebooks run ChromeOS and cannot"
            " run Windows games or Steam natively. They are not suitable for gaming."
            " Open with 1 sentence: 'Chromebooks can\\'t run Steam or Windows games —"
            " for gaming you\\'ll need a Windows laptop.' Then recommend Windows gaming"
            " laptops instead. Do NOT skip the product recommendations.]"
        )

    # "Windows Mac" / "Mac with Windows (only)" — acknowledge complexity
    if re.search(r"\b(mac\b|macbook|apple)\b", msg_lower) and \
       re.search(r"\b(windows\s+only|must\s+(run|have)\s+windows|need\s+windows|"
                 r"only\s+windows|windows\s+required)\b", msg_lower):
        enriched += (
            " [note: contradictory requirements — user wants Apple hardware but requires"
            " Windows-only software. M-series Macs can\\'t run Windows natively"
            " (Boot Camp no longer supported on Apple Silicon). Open with 1 sentence"
            " explaining this, then recommend Windows alternatives that have similar"
            " build quality (Dell XPS, HP Spectre, Lenovo ThinkPad). Do NOT skip recs.]"
        )

    return enriched
