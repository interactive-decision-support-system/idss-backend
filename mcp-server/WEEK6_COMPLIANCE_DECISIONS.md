# Week 6 Compliance Decisions

This document records decisions made for week6instructions.txt and week6tips.txt compliance, including gaps and their rationale.

---

## 1. Supabase vs PostgreSQL

### Decision
**PostgreSQL is used** instead of Supabase for the real-only product database.

### Rationale
- **Existing infrastructure**: The project already uses PostgreSQL + SQLAlchemy. Migrating to Supabase would require schema changes, connection config, and testing.
- **Local development**: PostgreSQL runs locally; no cloud dependency during development.
- **Timeline**: week6 focused on real-only data, scraping, KG enrichment, and removing fake products. Supabase migration was deferred.
- **Future path**: Supabase can be adopted later if team consolidation (e.g., Hannah’s Supabase DB + Thomas’s book DB) proceeds. A migration script could export scraped products to a Supabase-compatible CSV.

### References
- week6tips: "Supabase add 161 real COMPUTER products"; "I should use hannah supabase database + thomas book database - all in supabase"
- week6instructions: PostgreSQL + Neo4j for real scraped products

---

## 2. Google UCP (Universal Commerce Protocol)

### Decision
**UCP-style endpoints are implemented**; full Google UCP spec compliance is treated as a future enhancement.

### Implemented
- UCP endpoints: `/ucp/search`, `/ucp/get-product`, `/ucp/add_to_cart`, `/ucp/checkout`
- Schemas: `app/ucp_schemas.py` (UCPSearchRequest, UCPGetProductRequest, etc.)
- Agent-ready fields: shipping, return_policy, warranty, promotion_info in product responses
- Reference: https://github.com/Universal-Commerce-Protocol/ucp

### Gaps / Future work
- **Exact spec alignment**: Current implementation follows the UCP pattern; full Google UCP spec compliance (naming, optional fields, error codes) may need verification.
- **API-for-agent doc**: Optional script or doc listing UCP endpoints and request/response formats for AI agents (per week6tips tip 19).
- **Checkout / delivery**: Hannah’s checkout and delivery pieces (UCP add-to-cart + delivery) are owned by a different team member; integration pending.

### References
- week6tips: "Everything should be done and called in standard way thru google UCP"; "Assume any ai agent can call this api"
- Hannah: "checkout and add to cart (UCP google)"

---

## 3. Other Compliance Summary

| Area | Status | Notes |
|------|--------|-------|
| Real-only products | ✅ | No fake/seed; DB cleared on populate |
| Laptops + phones | ✅ | System76, Framework, Fairphone, BigCommerce, Shopify |
| Books | ⚠️ | B&N timeout; Open Library fallback added |
| Images | ✅ | Missing images removed (use `--keep-missing-images` to keep) |
| kg_features | ✅ | Backfill script run; 40+ products enriched |
| Shipping/return/warranty | ✅ | In descriptions; _enrich_with_policy in populate flow |
| 30+ features per product | ⚠️ | Scraped data ~10–15; kg_features add semantic features |
| Each product type own KG | ⚠️ | Single KG for all; domain-specific KGs possible later |

---

## 4. Run Commands

```bash
cd mcp-server
python scripts/populate_real_only_db.py --full
python scripts/backfill_kg_features.py
python scripts/build_knowledge_graph.py --clear
```

Optional: `--keep-missing-images` to retain products with placeholder images.
