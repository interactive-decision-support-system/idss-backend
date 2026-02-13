# Week 6+ Action Plan (from week6tips.txt)

This document turns the week6tips design notes into an actionable plan. **MCP is treated as a black box** from the AI agent’s perspective: we define and implement its inputs/outputs and capabilities; the agent calls standard APIs (e.g. UCP) and gets enriched responses without needing to know MCP internals.

---

## 1. Enriched MCP outputs (high priority)

**Goal:** MCP responses must be “agent-ready”: more than a list of products. Include delivery, return, shipping, warranty, and promotion so the AI agent does not need extra round-trips.

**References:** tips 5, 6, 7, 8, 20, 21, 29.

### 1.1 Schema (product discovery)

- **ProductSummary** (search results): add optional `shipping`, `return_policy`, `warranty`, `promotion_info`.
- **ProductDetail** (get product): add same fields; reuse existing `ShippingInfo` where applicable.
- **ShippingInfo** already exists; use it for `estimated_delivery_days`, `shipping_cost_cents`, `shipping_region`, `shipping_method`.

### 1.2 Populate in MCP (black box behavior)

- In **search** and **get_product**: for each product returned, attach:
  - **Delivery/ETA:** e.g. `ShippingInfo(estimated_delivery_days=5, shipping_method="standard", …)` (synthetic OK).
  - **Return policy:** e.g. “Free 30-day returns” (synthetic or from DB if added later).
  - **Warranty:** e.g. “1-year manufacturer warranty” (synthetic or from DB).
  - **Promotion:** e.g. “10% off through [date]” or null (synthetic or from DB).
- No change to MCP’s *logic* (still same search/get_product); only *response shape* is enriched.

### 1.3 UCP

- Ensure **UCP search** and **UCP get-product** responses expose the same enriched fields so any AI agent using UCP gets them (standard way, tip 19).

---

## 2. Standard protocol and “any AI agent”

**Goal:** Everything callable in a standard way via Google UCP; assume any AI agent can call search/product/cart/checkout.

**References:** tips 19, 29.

### 2.1 Actions

- Keep UCP endpoints as the standard surface: `/ucp/search`, `/ucp/get-product`, `/ucp/add_to_cart`, `/ucp/checkout` (and checkout-sessions if used).
- Document for the AI agent: which APIs to use (e.g. “use UCP search for product discovery; response includes shipping/return/warranty/promotion”).
- Optional: add a small **API-for-agent** doc or script that lists MCP/UCP endpoints, request/response formats, and that responses are enriched (per week6tips).

---

## 3. Reduce back-and-forth

**Goal:** Discovery response includes shipping/return/warranty/promotion so the agent does not need a second call to “get policies.”

**References:** tip 20.

### 3.1 Action

- Implement step 1 (enriched fields in search + get_product). No extra endpoint required; same search/get_product return more data.

---

## 4. Testing (beyond simple queries and flows)

**Goal:** Tests should cover enriched output and user flows mentioned in week6tips, not only simple price/brand filters.

**References:** tips 12, 54.

### 4.1 Enriched output

- **Test: search** – Response `data.products[]` includes (possibly optional) `shipping`, `return_policy`, `warranty`, or `promotion_info` (or that `metadata` carries them).
- **Test: get_product** – Response `data` includes the same enriched fields for a single product.

### 4.2 Flows to cover (unit/integration tests)

- **See similar items** – Covered by chat/recommendation flow; test that MCP search (or chat using MCP) can return “similar” products (e.g. same category or same brand); MCP remains black box (we test inputs/outputs, not internals).
- **Research** – Test that a “research” path (e.g. chat with “research”/“explain features”) returns `research_data` and uses product data; no need to open MCP internals.
- **Compare items** – Test that compare flow returns `comparison_data` with at least two products; MCP may be used to fetch product details.
- **Rate recommendations** – Test that the system accepts feedback (e.g. favorites) and that a subsequent recommendation or “rate” path completes without error.
- **Help with checkout** – Test **add_to_cart** and **checkout** (and UCP add_to_cart/checkout if used): add item to cart, then checkout; assert success and that response includes order + optional shipping info (already in OrderData).

### 4.3 Beyond simple filters

- Add at least one test that uses **multiple filters** (e.g. category + brand + price_max) or a **natural-language-style query** (e.g. query string with “gaming laptop under 2000”) and assert that MCP returns OK and non-empty (or expected) results when data exists. This aligns with “not only simple filter-based queries” (tip 12).

---

## 5. What not to change (MCP as black box)

- **Recommendations / “rates, compare, anything else”** – These are AI agent / chat side; MCP only provides search, get_product, add_to_cart, checkout. We do not move recommendation logic into MCP; we only ensure MCP’s outputs are enriched and standard (tips 22–23).
- **Query parsing** – Tips say “don’t use my query parser and query specificity files” in a specific context (Thomas); for this repo we keep existing MCP behavior and only enrich responses and add tests.
- **ACP/UCP as main chat path** – Frontend may still use POST /chat; UCP is for standard tool-call style. We only ensure UCP responses are enriched; we do not force chat to go through UCP.

---

## 6. Step-by-step implementation checklist

1. **Schemas**
   - [ ] Add to `ProductSummary`: `shipping: Optional[ShippingInfo]`, `return_policy: Optional[str]`, `warranty: Optional[str]`, `promotion_info: Optional[str]` (or a small PromotionInfo model).
   - [ ] Add same to `ProductDetail`.
   - [ ] Keep backward compatibility (optional fields, no extra="forbid" on these).

2. **Endpoints (MCP black box)**
   - [ ] In search (build `ProductSummary`): set `shipping`, `return_policy`, `warranty`, `promotion_info` (synthetic defaults if DB has no data).
   - [ ] In get_product (build `ProductDetail`): set same.
   - [ ] In cache path (get_product from cache): include these in cached payload and in response.
   - [ ] UCP: ensure UCP search/get-product responses map these fields so “any AI agent” gets them.

3. **Tests**
   - [ ] Test: search response has enriched fields on each product.
   - [ ] Test: get_product response has enriched fields.
   - [ ] Test: add_to_cart then checkout (help with checkout flow) returns success and order with shipping when applicable.
   - [ ] Test: at least one search with multiple filters or richer query (beyond single price/brand).
   - [ ] Optional: tests for chat flows that use “see similar items”, “compare”, “research”, “rate”, “help with checkout” (integration style, asserting response types and no 500).

4. **Docs**
   - [ ] Optional: short “APIs for AI agent” (or add to README): list UCP endpoints, that responses include delivery/return/warranty/promotion, and point to week6tips for design rationale.

---

## 7. Richer knowledge graph (key todo from week6tips)

**Goal:** Make the KG support complex queries (tip 16: if KG lacks features like "good for deep learning" we cannot map the query). **References:** tips 14, 16, 17, 23, 29.

**Steps:** (1) Pick domain e.g. laptops. (2) Gather Reddit-style complex query examples. (3) Extract feature concepts: `good_for_ml`, `good_for_web_dev`, `battery_life_hours`, `keyboard_quality`, `runs_linux_well`, etc. (4) Extend Neo4j KG schema (optional properties on Product/Laptop). (5) Extend PostgreSQL product metadata (columns or JSONB). (6) Backfill via LLM or manual tagging. (7) In kg_service and search: map query concepts to new filters. (8) Aim for 30+ features per product (tip 29); document in NEO4J_KNOWLEDGE_GRAPH.md. **Owner:** Juli + Hannah; Thomas consumes richer filters from MCP once they exist.

**See also:** `COMPLEX_QUERY_ROUTING.md` (routing) and `app/complex_query.py` (`is_complex_query`).

---

## 8. Complex-query handling / half-hybrid (helping Thomas)

**Goal:** Tip 71: for complex queries use universal_agent.py; for simple use current MCP filter path (no LLMs, faster).

**Two-branch design:** Simple = brand, price, category → MCP search. Complex = natural-language, "good for X" → UniversalAgent or LLM→filters.

**Implemented:** `app/complex_query.py` provides `is_complex_query(query, filters)` (heuristics: long text, multiple sentences, phrases like "good for", "need to run", "battery life"). **COMPLEX_QUERY_ROUTING.md** explains how Thomas can wire chat: when `is_complex_query(message)` True → call `UniversalAgent.process_message(message)`, use its `filters` for MCP search, return recommendations; when False → current flow.

**Done:** Chat path in `app/chat_endpoint.py` now branches on `is_complex_query(message)`: when True, calls `UniversalAgent.process_message(message)`, maps agent filters via `_agent_filters_to_search_filters`, runs `_search_ecommerce_products` with those filters, and returns recommendations (or a question/error). Richer KG (section 7) is implemented so complex filters can match in DB/KG.

---

## 9. Out of scope for this implementation

- KG enrichment, Supabase, and “161 real products” upload (separate tasks).
- Fine-tuning / RLHF / domain-specific models (research, not MCP code).
- Small-company outreach and email templates (process, not code).
- Removing or refactoring `query_parser` / `query_specificity` (team decision; we only add tests and enriched output).
- Bringing back “compare 2 products dropdown” from old IDSS repo (separate feature).

---

## 10. Success criteria

- Search and get_product responses include delivery/return/warranty/promotion (synthetic or from DB).
- UCP search and get-product return the same enriched info.
- New tests pass: enriched output, checkout flow, and at least one “beyond simple filters” query.
- MCP remains a black box: same APIs, richer responses; no change to recommendation logic inside MCP.
- **Richer KG (next):** At least one domain (e.g. laptops) has extended KG/DB schema and backfill for Reddit-style features (good_for_ml, battery_life, etc.).
- **Complex queries (Thomas):** Chat or agent can route complex messages to UniversalAgent (or LLM→filters) and simple to filter-only path; doc exists for Thomas.
