# IDSS Take-Home Exam (2026) — Question 1

**Question 1:** Agent flexibility — intent recognition and orchestration.

Copy sections into your **Google Doc** as needed. All code changes are on branch `baani/q1-intent-recognition`.

---

## 1. Supported post-recommendation intents (inventory)

Post-rec handling runs when `session.stage == STAGE_RECOMMENDATIONS` and `active_domain` is set.
Entry point: `process_chat` → `_handle_post_recommendation` in `agent/chat_endpoint.py`.

The routing pipeline has **three layers**, tried in order:

```
Message
  │
  ├─ 1. Fast-keyword guards (substring / exact match, no LLM, ~0ms)
  │      best_value · pros_cons · targeted_qa · compare · see_similar
  │      refine · brand quick-reply · research · add_to_cart (cart vocab)
  │
  ├─ 2. LLM router — detect_post_rec_intent() in comparison_agent.py
  │      Returns: compare | targeted_qa | refine | new_search
  │      Pre-LLM guards: _FOLLOWUP_STARTERS, short questions ≤6 words
  │
  └─ 3. Fallback — UniversalAgent.process_refinement() (LLM)
         Handles anything the router could not classify
```

### Intent inventory (before fixes)

| Intent | Detection method | Handler / outcome |
|--------|-----------------|-------------------|
| Prompt injection | `_is_prompt_injection()` | Blocked — canned reply |
| Popular QA cache | `_POPULAR_QA` key match | Cached answer + quick replies |
| Rate | Exact chip text | Rating prompt |
| **best_value** | `_FAST_BEST_VALUE_KWS` substring | Best-value narrative |
| **pros_cons** | `_FAST_PROS_CONS_KWS` substring | Price-spread compare narrative |
| **targeted_qa** | `_FAST_TARGETED_QA_KWS` substring | 1–2 winner products + reasoning |
| **compare** | `_FAST_COMPARE_KWS` substring | Side-by-side comparison cards |
| **refine** (see similar) | `_FAST_SEE_SIMILAR_KWS` substring | KG → SQL similar flow |
| **refine** (filters) | `_FAST_REFINE_KWS` + exact quick-reply branches | Re-search or follow-up |
| **add_to_cart** | `cart`/`bag`/`favorites`/`wishlist` + action verb | `cart_action`, favorites |
| **research** | `research`, `explain features`, … | RAG-style research |
| **checkout** | `checkout`, `pay`, `transaction` | Cart summary message |
| **new_search** | LLM router → `new_search` | Session reset → UniversalAgent |
| Fallback refine | No match above | `UniversalAgent.process_refinement()` |

### Gaps identified (before fixes)

| Failing phrase | Expected intent | Root cause |
|---------------|----------------|------------|
| `"Lay these out side by side"` | compare | `_FAST_COMPARE_KWS` had `"lay them out"` but not the anaphoric `"these"` variant |
| `"What are the differences between these?"` | compare / targeted_qa | LLM router occasionally returned `new_search`, wiping the session |
| `"I'll take the second one"` | add_to_cart | Guard required `cart`/`bag` vocabulary; purchase idioms not covered |
| `"I'll take it."` | add_to_cart (first product) | Same — no cart word present |
| `"I've had bad experiences with Dell"` | brand exclusion | `_excl_kw_pat` captured `"experiences"` as the brand token instead of `"Dell"` |
| `"them"` at sentence start in LLM fallback | anaphora detection | Fallback used substring `" them"` (requires leading space); missed sentence-initial position |

---

## 2. Intent taxonomy (target coverage)

| Intent | Example phrasings (3+) |
|--------|------------------------|
| **Search / new topic** | "Actually I want a gaming laptop instead"; "Forget that, show me books"; "Start over with SUVs under 30k" |
| **Refine filters** | "Show me something cheaper"; "Under $800 please"; "I need more RAM"; "Different brand" |
| **Compare (all shown)** | "Compare these"; "What are the differences between these?"; "Lay them out side by side"; "Lay these out side by side"; "Can you put them side by side?" |
| **Targeted Q&A** | "Which has the best battery?"; "Which should I pick for college?"; "Which is most durable?"; "Which one would you recommend?" |
| **Add to cart** | "Add the second one to my cart"; "I'll take the second one"; "I'll take it."; "Put the first in my bag"; "Give me that one" |
| **Exclude brand** | "No Dell"; "I've had bad experiences with Dell"; "Anything but HP"; "Avoid Apple"; "Skip Lenovo" |
| **See similar** | "Show me similar items"; "Something like the first one"; "More like option 2" |
| **Checkout** | "Checkout"; "I want to pay"; "Let's complete the purchase" |
| **Research** | "Summarize the reviews"; "Explain the specs"; "Check compatibility" |
| **Rate** | "Rate these recommendations"; "How good are these picks?" |

---

## 3. Implemented improvements

Six changes were made. The first three directly address the four exam failure cases; the latter three prevent regressions and add observability.

| # | Intent area | File | Change | Justification |
|---|-------------|------|--------|---------------|
| 1 | **Compare** | `agent/chat_endpoint.py` | Added `"lay these out"` to `_FAST_COMPARE_KWS` | Deterministic substring, zero latency, same cost as all other fast-path entries. `"these"` and `"them"` are distinct tokens; both need to be listed. |
| 2 | **Add to cart** | `agent/chat_endpoint.py` | Added module-level `_PURCHASE_IDIOMS_RE` and `_CASUAL_TAKE_DEFAULT_RE`; extended the add-to-cart detection guard | `_PURCHASE_IDIOMS_RE` covers `"I'll take the second one"` / `"give me the first"`. `_CASUAL_TAKE_DEFAULT_RE` is anchored (`^…$`) so it only fires when the **entire** stripped message is the idiom — avoids false positives inside longer sentences like `"I'll take it if it has 32GB RAM"`. |
| 3 | **Brand exclusion** | `agent/universal_agent.py` | Added `poor` to the keyword prefix set; removed `awful` (too broad — precedes adjectives not brands); added optional suffix `(?:\s+experiences?\s+with)?`; hoisted compiled pattern to module level | The old pattern `bad\s+(\w+)` captured `"experiences"` as the brand. The optional suffix skips the bridging phrase so the capture group lands on `"Dell"`. `awful` was excluded because `"awful battery life"` would incorrectly attempt to extract `"battery"` as a brand. False positives (e.g. `"poor performance"`) are filtered by the `_KNOWN_BRANDS_LIST` allowlist downstream. Open-ended sarcasm and indirect sentiment still fall through to the LLM semantic path (`_extract_excluded_brands_semantic`). |
| 4 | **Anaphora veto** | `agent/chat_endpoint.py` | Added `_message_references_shown_recommendation_set()` helper; downgrade `new_search` → `targeted_qa` when anaphora detected | Session reset is the most destructive action in the system — it wipes all filters and conversation state. Anaphoric references (`these`, `those`, `them`, ordinals) are a strong signal the user is asking about the **current** set, not starting fresh. `targeted_qa` is chosen over `refine` as the downgrade target because it answers a question about visible products rather than triggering a re-search. |
| 5 | **LLM router exception path** | `agent/comparison_agent.py` | Added `import re`; replaced `" them"` substring with `re.search(r'\b(these\|those\|them)\b')` | `" them"` requires a leading space, so `"them"` at sentence start (e.g. `"Them vs each other"`) was invisible to the guard. Word boundaries handle all positions. |
| 6 | **Observability** | `agent/chat_endpoint.py` | Added `post_rec_intent_resolved` structured log | Emits `router_layer` (`fast_keyword` vs `llm_router`), final `intent`, `anaphora_blocked_reset`, and a message preview after every intent resolution. One `grep` in staging logs is now enough to trace any routing regression without adding print statements. |

---

## 4. Unit tests

Thirteen new tests were added across two files. All use `unittest.mock` to stay offline (no DB, no LLM, no Redis).

| Test | File | What it asserts |
|------|------|-----------------|
| `test_post_rec_compare_lay_these_out_fast_path` | `test_chat_endpoint.py` | `"Lay these out side by side"` routes to compare via fast-keyword; LLM router is **never called** (asserted via `side_effect=AssertionError`). |
| `test_add_to_cart_ill_take_second_no_cart_keyword` | `test_chat_endpoint.py` | `"I'll take the second one"` (no cart/bag word) → `cart_action` for `prod-002`. |
| `test_add_to_cart_ill_take_it_defaults_to_first` | `test_chat_endpoint.py` | `"I'll take it."` (full message, anchored) → `cart_action` for `prod-001`. |
| `test_post_rec_anaphora_downgrades_new_search_no_session_reset` | `test_chat_endpoint.py` | LLM mocked to return `"new_search"`; anaphoric message → `reset_session` **never called**; response type is `"recommendations"`. |
| `test_genuine_new_search_without_anaphora_resets_session` | `test_chat_endpoint.py` | Fresh query with no anaphoric reference → `reset_session` **is called**; handler returns `None`. |
| `test_purchase_idiom_conditional_clause_not_add_to_cart` | `test_chat_endpoint.py` | `"I'll take it if it has 32GB RAM"` → `cart_action` is `None` (conditional clause blocks fullmatch). |
| `test_purchase_idiom_preference_statement_not_add_to_cart` | `test_chat_endpoint.py` | `"I want this to be under $1000"` → `cart_action` is `None` (no ordinal, no cart word). |
| `test_message_references_shown_recommendation_set` | `test_chat_endpoint.py` | Unit-tests the anaphora helper: True for demonstratives/comparative `them`/ordinals; False for fresh queries and informal `"one of them"` phrasing. |
| `test_excluded_brands_bad_experiences_with_brand_regex` | `test_universal_agent.py` | `_detect_excluded_brands("I've had bad experiences with Dell")` → `["Dell"]` with LLM patched out. |
| `test_excluded_brands_awful_adjective_not_captured` | `test_universal_agent.py` | `"awful battery life"` → `[]` — `"awful"` removed from keyword list to prevent adjective false positives. |
| `test_excluded_brands_poor_adjective_not_captured` | `test_universal_agent.py` | `"poor performance"` → `[]` — non-brand noun filtered by `_KNOWN_BRANDS_LIST` allowlist. |
| `test_excluded_brands_generic_sentiment_not_captured` | `test_universal_agent.py` | `"bad luck in general"` → `[]` — no known brand in capture group. |

---

## 5. Before / after evidence

### Test counts

| Suite | Before | After |
|-------|--------|-------|
| `agent/tests/` | 77 passed | **89 passed** (+12) |
| `mcp-server/tests/` | 527 passed | **527 passed** (unchanged) |
| **Full suite** | **604 passed** | **616 passed, 4 skipped** |

### New tests — actual run output

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

### Live site manual verification

Test these phrases on https://idss-web.vercel.app/ after asking for laptop recommendations:

| Phrase | Expected behaviour (after fix) |
|--------|-------------------------------|
| `"Lay these out side by side"` | Side-by-side comparison table appears |
| `"I'll take the second one"` | Second product added to cart |
| `"I've had bad experiences with Dell"` | Dell excluded from next search |
| `"Which of these has the best battery?"` | Agent answers about shown products (session not reset) |

---

## 6. Trade-offs

**Regex / keywords-first strategy**
Fast-path keyword matching is auditable, deterministic, and costs zero API calls. The risk is missing novel phrasing — mitigated by the LLM router as a second layer and `process_refinement` as a third. Adding phrases to keyword lists is a transparent, reviewable change unlike tuning embedding thresholds.

**Not replacing the LLM router with a similarity classifier**
A similarity-based classifier would require a labelled evaluation set and a chosen similarity threshold. Without those, it introduces opaque failure modes. The LLM router already has good coverage; the fixes here tighten the layers around it.

**Brand exclusion — structural regex vs full sentiment**
The `(?:\s+experiences?\s+with)?` suffix handles the fixed syntactic pattern `"<keyword> experiences with <Brand>"`. Arbitrary sentiment (`"Dell has burned me twice"`) is intentionally left to the LLM semantic path — adding open-ended cases to the regex would increase false-positive risk without a meaningful coverage gain.

**Anaphora veto — why `targeted_qa` and not `refine`**
`refine` triggers a product re-search, which may also be wrong. `targeted_qa` asks the agent to answer a question about the products already on screen — the safest conservative default when the only certainty is that the user is **not** starting over.

**`_CASUAL_TAKE_DEFAULT_RE` anchoring**
The pattern is anchored (`^…$`) so `"I'll take it if it has 32GB RAM"` does **not** trigger add-to-cart. Only a bare `"I'll take it."` or `"I'll take that"` with no qualifying clause matches — avoiding false purchases in conditional sentences.
