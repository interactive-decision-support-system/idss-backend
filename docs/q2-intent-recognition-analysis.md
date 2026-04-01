# Q2 — Intent Recognition & Orchestration Analysis

## Q2.1: Current Intent Inventory

Intent detection is split across **three layers**, each with its own keyword
lists, LLM prompts, and fallback logic. The table below catalogues every
intent the system currently handles, where it is defined, how it is detected,
and what phrasings are covered vs. missed.

### Layer 1 — Keyword fast-path (`chat_endpoint.py` `_handle_post_recommendation()`, lines 1086-1231)

Fires **before** any LLM call. Uses substring matching on `msg_lower`.

| Intent | Keywords / Patterns | What's Covered | What's Missing |
|---|---|---|---|
| `best_value` | "best value", "get best", "show me the best", "best pick" | Explicit "best value" requests | "bang for the buck", "most value for money", "what gives me the most for my money", "which is the smartest buy" |
| `pros_cons` | "tell me more about these", "pros and cons", "worth the price", "trade-off/trade off/tradeoff", "battery life on these", "what do you get for the extra" | Button-text exact matches, trade-off questions | "strengths and weaknesses", "upsides and downsides", "what's good and bad about these", "break it down for me" |
| `targeted_qa` | "which has the best …", "which is the most …", "which would you recommend", "which should I get/pick/choose", "best for college/work/gaming/students", "real-world difference" | Superlative winner questions | "if you had to pick one", "your top pick", "what would you go with", "the winner here is?" |
| `compare` | " vs ", "compare my/these/them/all", "which is better", "difference(s) between", "how do they differ", "contrast", "side by side", "detailed specs of each", GPU/RAM chips | Explicit compare + many ActionBar chips | "stack them up", "put them head to head", "how do these stack up", "line them up" |
| `see_similar` (→ refine) | "see similar", "similar items/laptops/products", "something similar", "similar to" | Button text and common variants | "anything like this", "more like the first one", "show me alternatives", "other options like X" |
| `refine` | "refine my search", "change budget", "different screen size/brand", "add a requirement", "inch", "increase my budget", "show me all laptops/books", use-case quick-replies, bare brand names | Quick-reply button texts, exact brand names | "narrow it down", "tighten the search", "I need it lighter", "actually make it cheaper", "something under $X" (only partial — "under $" is in comparison_agent's `_REFINE_KEYWORDS` but not in the fast-path) |
| `add_to_cart` | cart/favorites/wishlist/bag **+** add/put/save/get/take/want/buy | "add X to my cart", "save this" | **Ordinal references** ("add the second one") — detected later in the handler but not in the keyword gate. "I'll take it", "let me get that one", "put the first one in my bag" (no ordinal in keyword match) |
| `research` (→ other) | "research", "explain features", "check compatibility", "summarize reviews" | Button-text matches | "tell me more about X specifically", "deep dive on the Dell", "what do reviewers say" |

### Layer 2 — LLM intent router (`comparison_agent.py` `detect_post_rec_intent()`, lines 119-198)

Fires only when **no** keyword fast-path matched. Returns one of:
`compare`, `targeted_qa`, `refine`, `new_search`.

| Intent | How Detected | Fallback (on LLM failure) |
|---|---|---|
| `new_search` | LLM + regex heuristic: no anaphoric refs ("these"/"them") AND (spec keywords + budget markers OR explicit new-intent phrases) | Heuristic checks for RTX/Ryzen/i7 + "$" + absence of "these"/"them" |
| `refine` | LLM classification | `_REFINE_KEYWORDS` substring match |
| `targeted_qa` | LLM (default when unsure) | Superlative signal keywords |
| `compare` | LLM — only when user explicitly asks for side-by-side of ALL products | No keyword fallback for compare |

**Gap:** No `explain_fit` / `why_not` intent. If a user asks "Why is the Dell
no longer recommended?" after a refine, the LLM defaults to `targeted_qa`,
which returns product cards instead of explanatory reasoning about changed
constraints.

### Layer 3 — Post-rec refinement (`universal_agent.py` `process_refinement()`, lines 1688-1753)

Fires as the **last resort** for unmatched post-rec messages. Uses structured
LLM extraction via `RefinementClassification`.

| Intent | Behavior |
|---|---|
| `refine_filters` | Merges new criteria into `self.filters`, re-searches |
| `domain_switch` | Resets session, routes to new domain agent |
| `new_search` | Clears filters, applies new criteria, re-searches |
| `action` | Returns `not_refinement` — caller handles |
| `other` | Returns `not_refinement` — caller handles |

**`new_search` duplication:** Layer 2 resets the entire session and returns
`None` (falls through to `UniversalAgent` from scratch). Layer 3 clears only
`filters` and re-uses the existing agent instance. Same label, different
semantics.

### Post-intent handlers (keyword-matched, no intent label)

These fire **after** the intent switch but are matched by raw keyword
substring, not by the intent classification:

| Handler | Trigger | Line |
|---|---|---|
| Checkout | "checkout", "pay", "transaction" | 2544 |
| Rate recommendations | "rate" + ("recommendation" OR "these") | 2569 |
| "Anything else" help | "anything else" | 2479 |
| Research | "research", "explain features", "check compatibility", "summarize reviews" | 2492 |

---

## Q2.2: Proposed Intent Taxonomy

The taxonomy below is designed for **scalability**: new intents can be added
as rows without restructuring the router. Each intent has a unique label, a
clear scope boundary, and 3+ example phrasings.

| # | Intent | Scope | Example Phrasings |
|---|---|---|---|
| 1 | `search` | Brand-new product search (no prior context) | "I need a laptop for college", "Show me gaming laptops under $1500", "Looking for a reliable SUV" |
| 2 | `refine` | Adjust current search filters (budget, brand, specs) | "Make it cheaper", "Switch to Dell", "At least 16GB RAM", "Something lighter" |
| 3 | `new_search` | Start completely fresh within same/different domain | "Forget that, show me mystery novels", "Actually I want a gaming rig", "Start over" |
| 4 | `domain_switch` | Switch product category entirely | "Show me books instead", "Let's look at cars", "Switch to phones" |
| 5 | `compare` | Side-by-side comparison of all/named products | "Compare these", "X vs Y", "How do they differ?", "Lay them out side by side", "Stack them up" |
| 6 | `targeted_qa` | "Which is best at X?" — 1-2 winners with reasoning | "Which has the best display?", "Which should I get for gaming?", "Your top pick?", "If you had to pick one" |
| 7 | `best_value` | Identify the single best bang-for-buck product | "Best value?", "Which is the smartest buy?", "Bang for the buck" |
| 8 | `pros_cons` | Prose explanation of trade-offs across products | "Pros and cons", "Strengths and weaknesses", "What's good and bad?", "Break it down for me" |
| 9 | `explain_fit` | Why a product does/doesn't fit after filter changes | "Why was the Dell dropped?", "Why is that one no longer recommended?", "How does this still fit my needs?" |
| 10 | `see_similar` | Show alternatives to a specific product | "Show me similar", "Anything like this?", "Alternatives to the MacBook", "More like the first one" |
| 11 | `add_to_cart` | Add a specific product to cart/favorites | "Add the second one to my cart", "I'll take the Dell", "Save that one", "Put it in my bag" |
| 12 | `research` | Deep-dive on one product (reviews, specs, compatibility) | "Research the first one", "What do reviewers say?", "Deep dive on the HP", "Tell me more about that specific one" |
| 13 | `checkout` | View cart / proceed to payment | "Checkout", "Ready to pay", "Complete my purchase", "What's in my cart?" |
| 14 | `rate` | Rate current recommendations | "Rate these", "These are great", "Not what I was looking for", "5 stars" |
| 15 | `help` | Meta: what can the system do? | "What can you do?", "Help", "What are my options?", "Anything else?" |
| 16 | `greeting` | Conversational opener | "Hi", "Hello", "Hey there" |

### Design notes

- **`explain_fit`** is the missing intent from the current system. It fills the
  gap where a user asks *why* a previously-shown product was dropped after a
  refinement — currently misrouted into recommendation-oriented post-rec logic.
- **`see_similar`** is promoted from a sub-case of `refine` to its own intent.
  The behavior (KG traversal for related products) is fundamentally different
  from filter refinement.
- **`checkout`** and **`rate`** are separated from the keyword-only handlers
  and given proper intent labels for consistent routing.
- The taxonomy is **flat** (no nesting) for simpler routing logic and easier
  LLM classification.

---

## Q2.3: Implementation — Improving 3 Intents

### Strategy: keyword expansion + regex (no additional LLM calls)

**Justification:** The keyword fast-path exists specifically to avoid the
latency and cost of an LLM call for predictable patterns. The phrasings being
missed are *predictable natural-language variants* — not ambiguous cases that
need semantic understanding. Adding regex patterns to the existing fast-path
is:

1. **Zero additional latency** — no LLM round-trip
2. **Zero additional cost** — no API call
3. **Deterministic** — same input always produces same routing
4. **Least disruptive** — extends the existing pattern, no architectural change

We reserve the LLM fallback for genuinely ambiguous messages.

### Intent 1: `add_to_cart` — ordinal and indirect purchase intent

**Problem:** "Add the second one to my cart" and "I'll take it" are not caught
by the keyword gate because the current logic requires both a container word
(cart/favorites/wishlist/bag) AND an action word. Ordinal-only references and
casual purchase idioms are missed.

**Fix:** Add a regex pattern that catches ordinal product references with
purchase intent, and casual purchase phrases like "I'll take it" / "let me get
that one".

### Intent 2: `explain_fit` (NEW) — fit-analysis after refinement

**Problem:** After refining search filters, a user might ask "Why is the Dell
no longer recommended?" This gets routed to `targeted_qa` which returns
product cards, not the explanatory reasoning the user wants.

**Fix:** Add a new `explain_fit` keyword set in the fast-path that catches
"why is X no longer", "why was X dropped", "how does X still fit", and routes
to a dedicated handler that provides filter-change reasoning.

### Intent 3: `pros_cons` — natural paraphrases

**Problem:** "Strengths and weaknesses", "upsides and downsides", "what's good
and bad about these" all miss the keyword fast-path and fall through to the
LLM, which may misclassify them.

**Fix:** Expand `_FAST_PROS_CONS_KWS` with natural paraphrases.

See the code changes in this PR for the implementation.
