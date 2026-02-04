# Meeting Executive Summary (Feb 2026)

## Goal

Deliver a clear, meeting-ready, one‑page overview of the MCP + IDSS system: architecture, key flows, current status, and next steps.

## Architecture (High Level)

- **Frontend (idss-web)** calls `POST /chat` on the MCP server (port 8001).
- **MCP Server** routes by domain:
  - **Vehicles** → IDSS API (port 8000) for interview + ranking.
  - **Laptops/Books** → MCP interview flow + PostgreSQL search.
- **Data Sources**:
  - Vehicles: SQLite + FAISS (IDSS data).
  - Laptops/Books: PostgreSQL (mcp_ecommerce).

## Key Flows

1. **/chat interview (laptops/books)**
   - MCP detects domain → asks slot-based questions → gathers filters → returns recommendations.
2. **/api/search-products**
   - Normalizes query → extracts filters → routes to data source → returns results.
3. **/api/get-product + /api/add-to-cart**
   - MCP handles MCP/UCP/Tool protocol mapping and returns structured responses for the agent.

## Current Status (What’s Done)

- **Interview alignment** for laptops/books (slot order + follow-up questions).
- **Hard + soft constraints** for electronics (brand/product_type/gpu/cpu/price + soft prefs).
- **/chat enforcement** for laptops/books (interview-first flow).
- **Protocol documentation** with quick examples and request/response mapping.
- **Frontend integration guidance** for idss-web.

## Personas (Target Users)

- **Budget‑Conscious Student**: Needs a reliable laptop for school under $1000; values portability and battery life.
- **Gaming Enthusiast**: Wants a high‑performance gaming laptop/PC under $2000; cares about GPU/CPU and compatibility.
- **Casual Reader**: Looks for affordable genre books ($10–$30) and quick discovery of similar titles.

## Sample Task Set (20 Concrete Cases) + Success Metrics

1. **Find a student laptop under $1000** → Success: 3+ recommendations with `price_max_cents <= 100000`.
2. **Gaming laptop under $2000 with NVIDIA GPU** → Success: all results include `gpu_vendor = NVIDIA` and price cap.
3. **Lightweight work laptop, 13–14 inch** → Success: results include “portable/lightweight” tags or screen size filter.
4. **Apple MacBook for creative work** → Success: brand = Apple; use_case = Creative.
5. **Dell school laptop $1000–$2000** → Success: brand = Dell; price range applied.
6. **Budget Chromebook under $500** → Success: product_type/OS indicates ChromeOS; price cap.
7. **Mystery books $10–$20** → Success: genre/subcategory = Mystery; price range $10–$20.
8. **Sci‑Fi books under $15** → Success: genre = Sci‑Fi; price_max_cents <= 1500.
9. **Non‑fiction books any price** → Success: genre = Non‑Fiction; no price filter required.
10. **Romance books $20–$30** → Success: genre = Romance; price range applied.
11. **Add specific product to cart** → Success: cart item_count increments and total updates.
12. **Get product details by ID** → Success: response includes requested fields only.
13. **Multi‑turn laptop interview (use_case → brand → budget)** → Success: 3 questions asked, then recommendations.
14. **Multi‑turn book interview (genre → budget)** → Success: 2 questions asked, then recommendations.
15. **Search with long multi‑sentence query** → Success: filters extracted; interview triggered if missing slots.
16. **Electronics hard constraints (brand + price + GPU)** → Success: results respect all constraints.
17. **Soft preferences (portable/durable)** → Success: soft prefs stored in `_soft_preferences` for ranking.
18. **Vehicle query routes to IDSS** → Success: `/chat` response from IDSS backend.
19. **Stateless search returns results (no session_id)** → Success: recommendations returned without interview.
20. **UCP protocol mapping for search/get‑product** → Success: UCP requests map to MCP and succeed.

Success Metrics (for the above):

- **Precision**: ≥80% of results satisfy explicit filters.
- **Interview completion**: ≥90% of flows reach recommendations within 3 questions (laptops) / 2 questions (books).
- **Latency**: p95 ≤ 1000ms for search; p95 ≤ 200ms for get_product.
- **Coverage**: at least 3 recommendations in each response row when inventory permits.

## Open Gaps (Week4 Alignment)

- Multi‑turn robustness evaluation tests (only test scripts + unit tests exist).
- Personas, sample tasks, and success metrics not documented.
- Real scraped products / database sync issues unresolved.
- Knowledge graph consolidation, supplier integration, and payments still pending.

## Recommended Next Steps

1. Add personas + sample task set (20 concrete cases) with success metrics.
2. Create multi‑turn evaluation suite (long queries, follow‑ups, context retention).
3. Fix scraped product ingestion + database consistency.
4. Align KG consolidation plan and supplier integration roadmap.

## Where to Look for Details

- **Deep technical reference:** `API_PROTOCOL_DOCUMENTATION.txt`
- **Setup + endpoints:** `README.md`
- **Future architecture + gaps:** `FUTURE_WORK.md`
