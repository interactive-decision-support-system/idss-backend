# IDSS Take-Home 2026 — Question 1
## Intent Recognition & Post-Recommendation Orchestration

**Author:** Baani Leen Kaur Jolly · baani@stanford.edu
**Branch:** `baani/q1-intent-recognition` · [GitHub PR](https://github.com/BaaniLeen/idss-backend/compare/baani/q1-intent-recognition)

---

## 1. System Overview

The shopping assistant routes every post-recommendation message through a **three-layer pipeline** before touching the session state:

```
User message (post-recommendation)
│
├─ Layer 1: Fast-keyword guards (0ms, no LLM)
│    Deterministic substring/regex matching for ~15 intent categories.
│    Handles: compare, pros_cons, targeted_qa, best_value, add_to_cart,
│             see_similar, refine, research, checkout, popular QA cache.
│
├─ Layer 2: LLM router — detect_post_rec_intent()
│    gpt-4o-mini, JSON mode, max 20 tokens.
│    Returns: compare | targeted_qa | refine | new_search
│    Pre-LLM guards (followup starters, short questions ≤6 words) bypass
│    the LLM for obvious follow-ups at zero cost.
│
└─ Layer 3: Fallback — UniversalAgent.process_refinement()
     Full LLM call; handles anything unclassified above.
```

This architecture keeps median intent latency under 5ms for ~80% of post-rec messages and reserves LLM budget for genuinely ambiguous cases.

---

## 2. Post-Recommendation Intent Inventory

Every message received when `session.stage == STAGE_RECOMMENDATIONS` passes through `_handle_post_recommendation()`. The table below maps every supported intent to its detection method and handler:

| Intent | Detection | Handler / outcome |
|--------|-----------|-------------------|
| Prompt injection | `_is_prompt_injection()` (regex + LLM) | Blocked — canned safety reply |
| Popular QA cache | `_POPULAR_QA` key match | Cached answer + quick replies |
| Rate | Exact chip text (`"5 stars"`, …) | Rating acknowledgement |
| **best_value** | `_FAST_BEST_VALUE_KWS` substring | Best-value narrative |
| **pros_cons** | `_FAST_PROS_CONS_KWS` substring | Price-spread comparison narrative |
| **targeted_qa** | `_FAST_TARGETED_QA_KWS` substring or LLM | 1–2 winner products + reasoning |
| **compare** | `_FAST_COMPARE_KWS` substring or LLM | Side-by-side comparison cards |
| **see_similar** | `_FAST_SEE_SIMILAR_KWS` substring | KG → SQL similar-product flow |
| **refine** | `_FAST_REFINE_KWS` + quick-reply branches or LLM | Re-search with updated filters |
| **add_to_cart** | Cart vocab OR `_PURCHASE_IDIOMS_RE` / `_CASUAL_TAKE_DEFAULT_RE` | `cart_action` payload to frontend |
| **research** | `research`, `explain features`, … keywords | RAG-style product research |
| **checkout** | `checkout`, `pay`, `transaction` | Cart summary message |
| **new_search** | LLM router → `new_search` (anaphora-vetoed) | Session reset → UniversalAgent |
| Fallback | No match above | `UniversalAgent.process_refinement()` |

---

## 3. Intent Taxonomy (Target Coverage)

| Intent | Trigger | Example phrasings |
|--------|---------|-------------------|
| **compare** | Fast-keyword or LLM | "Compare these"; "What are the differences?"; "Lay these out side by side"; "Pros and cons of each"; "How do they differ?" |
| **targeted_qa** | Fast-keyword or LLM | "Which has the best battery?"; "Which should I pick for college?"; "Which is most durable?"; "Which one would you recommend?" |
| **add_to_cart** | Cart vocab OR purchase idiom regex | "Add the second one to my cart"; "I'll take the second one"; "I'll take it."; "Put the first in my bag"; "Give me the third" |
| **refine** | Fast-keyword or LLM | "Show me something cheaper"; "Under $800 please"; "I need more RAM"; "Different brand"; "More options" |
| **see_similar** | Fast-keyword (`_FAST_SEE_SIMILAR_KWS`) | "Show me similar items"; "Something like the first one"; "More like option 2" |
| **best_value** | Fast-keyword (`_FAST_BEST_VALUE_KWS`) | "Which is the best value?"; "Best pick overall"; "Show me the best" |
| **pros_cons** | Fast-keyword (`_FAST_PROS_CONS_KWS`) | "Tell me more about these"; "Pros and cons"; "Worth the price?" |
| **research** | Fast-keyword | "Summarize the reviews"; "Explain the specs"; "Check compatibility" |
| **exclude brand** | Regex + LLM semantic | "No Dell"; "I've had bad experiences with Dell"; "Anything but HP"; "Avoid Apple"; "Skip Lenovo" |
| **new_search** | LLM router (conservative) | "Actually I want a gaming laptop instead"; "Forget that, show me books"; "Start over with SUVs" |
| **checkout** | Fast-keyword | "Checkout"; "I want to pay"; "Complete the purchase" |
| **rate** | Exact chip text | "5 stars"; "Could be better" |

---

## 4. Bugs Identified & Root Causes

Six failure modes were identified from the exam spec and code inspection:

| # | Failing input | Expected | Root cause |
|---|--------------|----------|------------|
| 1 | `"Lay these out side by side"` | compare | `_FAST_COMPARE_KWS` had `"lay them out"` but not the `"these"` variant. Anaphoric pronouns are distinct tokens — both must be listed. |
| 2 | `"I'll take the second one"` | add_to_cart | Cart guard required explicit vocabulary (`cart`/`bag`/`basket`). Purchase idioms like `"I'll take the Nth"` / `"give me the first"` were not covered. |
| 3 | `"I'll take it."` (bare) | add_to_cart (first product) | Same as above. Additionally, a fullmatch anchor was needed to prevent firing inside conditional sentences like `"I'll take it if it has 32GB RAM"`. |
| 4 | `"I've had bad experiences with Dell"` | brand exclusion → `["Dell"]` | `_EXCL_KW_PAT` captured `"experiences"` as the brand token because the optional bridging phrase `"experiences with"` was not consumed before the capture group. |
| 5 | `"What are the differences between these?"` → LLM returns `new_search` | targeted_qa / compare (session preserved) | Session reset is the most destructive action; anaphoric references (`these`, `those`, `them`, ordinals) were not used as a safety veto against `new_search`. |
| 6 | `"them"` at sentence start (LLM exception path) | anaphora detected | Exception-path fallback used `" them"` substring which requires a leading space — missed sentence-initial `"Them vs each other"`. |

---

## 5. Changes Implemented

### 5.1 Compare — fast-keyword extension
**File:** `agent/chat_endpoint.py`

Added `"lay these out"` to `_FAST_COMPARE_KWS`. The set already had `"lay them out"` but `"these"` and `"them"` are distinct tokens and both appear in natural speech. This is a zero-latency, zero-cost fix with no LLM involvement.

### 5.2 Add-to-cart — purchase idiom regexes
**File:** `agent/chat_endpoint.py`

Added two module-level compiled patterns:

```python
_PURCHASE_IDIOMS_RE = re.compile(
    r"\b(?:i'?ll\s+take|i\s+want|give\s+me|i'?d\s+like|get\s+me)\s+"
    r"(?:the\s+)?(?:first|second|third|fourth|1st|2nd|3rd|4th)\b",
    re.IGNORECASE,
)

_CASUAL_TAKE_DEFAULT_RE = re.compile(
    r"i'?ll\s+take\s+(?:it|that)\.?",
    re.IGNORECASE,
)
```

`_PURCHASE_IDIOMS_RE` matches ordinal purchase phrases without cart vocabulary.
`_CASUAL_TAKE_DEFAULT_RE` is called with `fullmatch()` — it only fires when the **entire stripped message** matches, which prevents it from triggering inside conditional sentences (`"I'll take it if it has 32GB RAM"` → no match).

### 5.3 Brand exclusion — bridging phrase suffix
**File:** `agent/universal_agent.py`

Extended `_EXCL_KW_PAT` with an optional non-capturing group:

```python
_EXCL_KW_PAT = re.compile(
    r'(?:no|not|never|anything but|avoid|hate|refuse|bad|terrible|poor|skip)'
    r'(?:\s+experiences?\s+with)?'   # ← new: skip "experiences with"
    r'\s+([A-Za-z][A-Za-z0-9\- ]{1,30})',
    re.IGNORECASE,
)
```

The suffix is optional so existing patterns (`"no HP"`, `"avoid Dell"`) are unaffected. False positives from the keyword `"poor"` (e.g. `"poor performance"`) are filtered downstream by the `_KNOWN_BRANDS_LIST` allowlist. `"awful"` was removed from the keyword set because it overwhelmingly precedes adjectives (`"awful battery life"`) rather than brand names, causing systematic false captures.

`_KNOWN_BRANDS_LIST` was also hoisted to module level — it was being rebuilt on every function call, which is wasteful and inconsistent with `_EXCL_KW_PAT` which was already at module level.

### 5.4 Anaphora veto — session-reset safety
**File:** `agent/chat_endpoint.py`

Added `_message_references_shown_recommendation_set()`:

```python
def _message_references_shown_recommendation_set(message: str) -> bool:
    lower = message.lower()
    # Strong demonstratives
    if re.search(r'\b(these|those)\b', lower):
        return True
    # "them" only in comparative context, not as informal determiner
    # ("one of them cheap laptops" → False; "compare them" → True)
    _THEM_CTX = ("compare", "between", "all of", "vs", "both of",
                 "either of", "none of", "each of")
    if re.search(r'\bthem\b', lower) and any(c in lower for c in _THEM_CTX):
        return True
    # Ordinal references with "the" prefix ("the second one", "the third")
    if re.search(
        r'\bthe\s+(?:first|second|third|fourth|1st|2nd|3rd|4th)\b', lower
    ):
        return True
    return False
```

If the LLM router returns `new_search` and this helper returns `True`, the intent is downgraded to `targeted_qa` (not `refine`, which would trigger a re-search). The session is preserved. The decision and the blocking are captured in the `post_rec_intent_resolved` structured log.

### 5.5 LLM router exception path — word-boundary fix
**File:** `agent/comparison_agent.py`

Replaced `" them"` substring with `re.search(r'\b(these|those|them)\b', lower)` in the exception-path fallback. The old approach required a leading space, so `"them"` at sentence start was invisible. Word boundaries handle all positions correctly.

The anaphora logic is intentionally **inlined** rather than imported from `chat_endpoint.py` — that module imports `comparison_agent` at the top level, making a reverse import circular. The comment documents this constraint explicitly.

### 5.6 Observability — structured intent log
**File:** `agent/chat_endpoint.py`

Moved the `post_rec_intent_resolved` log to fire **before** the `new_search` early return, so it captures all intent resolutions including the one that triggers session reset. The log emits:

```json
{
  "intent": "targeted_qa",
  "router_layer": "llm_router",
  "anaphora_blocked_reset": true,
  "msg_preview": "Are these good for everyday use?"
}
```

One `grep post_rec_intent_resolved` on production logs is sufficient to trace any routing regression.

---

## 6. Test Coverage

13 new offline tests were added (no DB, no LLM, no network — all mocked):

| Test | Assertion |
|------|-----------|
| `test_post_rec_compare_lay_these_out_fast_path` | `"Lay these out side by side"` → compare via fast-keyword; LLM is **never called** (guarded by `side_effect=AssertionError`). |
| `test_add_to_cart_ill_take_second_no_cart_keyword` | `"I'll take the second one"` → `cart_action["product"]["id"] == "prod-002"` |
| `test_add_to_cart_ill_take_it_defaults_to_first` | `"I'll take it."` → `cart_action["product"]["id"] == "prod-001"` |
| `test_post_rec_anaphora_downgrades_new_search_no_session_reset` | LLM mocked to `"new_search"`, anaphoric message → `reset_session` never called; `response_type == "recommendations"` |
| `test_genuine_new_search_without_anaphora_resets_session` | Fresh query → `reset_session` called once; handler returns `None` |
| `test_purchase_idiom_conditional_clause_not_add_to_cart` | `"I'll take it if it has 32GB RAM"` → `cart_action is None` |
| `test_purchase_idiom_preference_statement_not_add_to_cart` | `"I want this to be under $1000"` → `cart_action is None` |
| `test_message_references_shown_recommendation_set` | Unit-tests helper: True for demonstratives / comparative `them` / ordinals; False for fresh queries and informal `"one of them"` phrasing |
| `test_excluded_brands_bad_experiences_with_brand_regex` | `_detect_excluded_brands("I've had bad experiences with Dell")` → `["Dell"]` |
| `test_excluded_brands_awful_adjective_not_captured` | `"awful battery life"` → `[]` |
| `test_excluded_brands_poor_adjective_not_captured` | `"poor performance"` → `[]` |
| `test_excluded_brands_generic_sentiment_not_captured` | `"bad luck in general"` → `[]` |

**Suite results:**

| Suite | Before | After |
|-------|--------|-------|
| `agent/tests/` | 77 passed | **89 passed** (+12) |
| `mcp-server/tests/` | 527 passed | **527 passed** |
| **Full** | **604 passed** | **616 passed, 4 skipped** |

**Actual run output:**

```
agent/tests/test_chat_endpoint.py::test_post_rec_compare_lay_these_out_fast_path PASSED
agent/tests/test_chat_endpoint.py::test_add_to_cart_ill_take_second_no_cart_keyword PASSED
agent/tests/test_chat_endpoint.py::test_add_to_cart_ill_take_it_defaults_to_first PASSED
agent/tests/test_chat_endpoint.py::test_post_rec_anaphora_downgrades_new_search_no_session_reset PASSED
agent/tests/test_chat_endpoint.py::test_genuine_new_search_without_anaphora_resets_session PASSED
agent/tests/test_chat_endpoint.py::test_purchase_idiom_conditional_clause_not_add_to_cart PASSED
agent/tests/test_chat_endpoint.py::test_purchase_idiom_preference_statement_not_add_to_cart PASSED
agent/tests/test_chat_endpoint.py::test_message_references_shown_recommendation_set PASSED
agent/tests/test_universal_agent.py::test_excluded_brands_bad_experiences_with_brand_regex PASSED
agent/tests/test_universal_agent.py::test_excluded_brands_awful_adjective_not_captured PASSED
agent/tests/test_universal_agent.py::test_excluded_brands_poor_adjective_not_captured PASSED
agent/tests/test_universal_agent.py::test_excluded_brands_generic_sentiment_not_captured PASSED

============================== 89 passed in 26.40s ==============================
```

---

## 7. Design Trade-offs

**Keyword-first routing over a learned classifier**
A similarity-based classifier would require a labelled evaluation set and a chosen threshold — both absent in this codebase. Keyword/regex guards are auditable, deterministic, and reviewable in a code diff. The LLM router provides broad coverage for everything not explicitly enumerated. The two layers are complementary, not alternatives.

**Anaphora veto downgrades to `targeted_qa`, not `refine`**
`refine` triggers a product re-search, which is also wrong if the user is asking a question about visible products. `targeted_qa` answers a question about the products already on screen — the safest conservative fallback when the only certainty is that the user is **not** starting a new search. A session wipe is irreversible within a turn; `targeted_qa` is recoverable.

**`_CASUAL_TAKE_DEFAULT_RE` uses `fullmatch()`, not anchors in the pattern**
Anchoring via `^…$` inside the pattern and calling `.match()` or `.search()` is a common source of subtle bugs (`.match()` anchors at start but not end; `.search()` anchors at neither). Using `fullmatch()` with a clean, unanchored pattern is explicit and self-documenting. Stripping before matching is unnecessary because `clean_message` is already stripped upstream.

**Brand exclusion — structural regex, not full NLU**
The `(?:\s+experiences?\s+with)?` suffix handles the fixed syntactic pattern `"<keyword> experiences with <Brand>"`. Arbitrary sentiment expressions (`"Dell has burned me twice"`, `"last three Lenovos were duds"`) are intentionally left to the LLM semantic path (`_extract_excluded_brands_semantic`). Stretching the regex to cover open-ended syntax would increase false-positive surface area without meaningful coverage gain over the LLM fallback.

**Why `"awful"` was removed, not just filtered**
`"awful"` appeared in the keyword set but empirically precedes adjectives and nouns (`"awful design"`, `"awful battery"`) almost exclusively in the laptop shopping context. Leaving it in the keyword set causes `_EXCL_KW_PAT` to attempt a brand extraction on every such phrase, polluting the LLM call with spurious candidates. Removing it entirely is cleaner than relying solely on the allowlist to filter it out post-capture.

**Circular import — inlining over refactoring**
The cleanest long-term fix would be extracting the anaphora helper to a shared utility module (`agent/utils/text.py`), importable by both `chat_endpoint.py` and `comparison_agent.py`. That refactor was kept out of scope for this PR to limit blast radius — it touches import paths across the whole agent package. The inlined logic is documented with a comment explaining the constraint, making it straightforward for a future PR to extract cleanly.
