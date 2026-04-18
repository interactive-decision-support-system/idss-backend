# KG ↔ products_enriched contract

This doc is the shared source of truth for how the knowledge graph relates
to enriched catalog data. Issue #52 rewrote the rules below after #51
surfaced a silent-zero-score bug (reader asked for a property the writer
never set; scorer fell to 0 without erroring).

## The five rules

1. **Direction is one-way: `products_enriched → KG`.** KG builders read
   enriched rows; they do not re-derive features from raw title/description
   text. Enrichment is its own pipeline under `app.enrichment` with LLM
   agents writing per-strategy rows to `products_enriched`.

2. **The Cypher scorer defines the property set.** Every `p.<name>` the
   reader (`kg_service._build_cypher_query`) touches must be producible
   by either: an identity field from raw `products`, a known raw attribute
   key (`registry.KNOWN_RAW_ATTRIBUTE_KEYS`), or a flattening rule in
   `app.kg_projection.FLATTENING_RULES` / `KEY_PATTERNS`. The offline
   `tests/test_kg_contract.py::test_cypher_referenced_properties_are_producible`
   asserts this; a new reader reference that isn't producible breaks that
   test and therefore CI.

3. **Per-`(merchant_id, strategy)` keying.** One KG "instance" per pair,
   mirroring how `MerchantAgent` and `products_enriched` are keyed. In
   Neo4j this is property-based: every `:Product` node carries
   `merchant_id` and `kg_strategy`, uniqueness is `(product_id,
   merchant_id, kg_strategy)`, and `search_candidates` hard-filters on
   both. Two merchants with the same `product_id` don't collide. Two
   strategies on the same merchant give two separate node populations.

4. **Rebuild order.** `raw ingest → enrichment → KG sync → vector index`.
   Anything upstream needs to land before anything downstream; there's no
   partial-rebuild fast path in this contract. CI and the scheduled
   scrape (`scripts/run_scheduled_scrape.sh`) run them in that order.

5. **Identity vs derived split.** Identity fields (`product_id`, `name`,
   `brand`, `category`, `subcategory`, `price`, `description`, `image_url`)
   come from raw `products`; derived fields come only from
   `products_enriched`. At read time they're joined; at write time each
   table owns its fields — no merge semantics, no COALESCE fallback. The
   `enriched_reader.combine_raw_and_enriched` helper asserts the disjoint-keys
   invariant so a violating writer fails loudly.

## What lives where

| Artifact | Location | Notes |
| --- | --- | --- |
| Projection rules | `mcp-server/app/kg_projection.py` | Identity fields + per-strategy flattening rules + `project()` + `cypher_referenced_properties()` |
| Contract drift test | `mcp-server/tests/test_kg_contract.py` | Offline; asserts reader refs are producible. Negative test covers injected drift. |
| Cypher reader | `mcp-server/app/kg_service.py` | `_build_cypher_query` + `search_candidates`. Threshold and tenancy bind via parameters. |
| Cypher writer | `mcp-server/app/knowledge_graph.py` | `create_laptop_node` / `create_book_node` consume `projection` + `(merchant_id, kg_strategy)` kwargs. |
| Orchestrator | `mcp-server/scripts/build_knowledge_graph.py` | `--merchant-id` + `--strategy` flags. Batches enriched via `hydrate_batch`. |
| Score plumbing | `mcp-server/app/merchant_agent.py` | Min-max normalizes KG scores onto `Offer.score`; per-term breakdown on `Offer.score_breakdown`. |

## Adding a new enrichment strategy that contributes KG properties

1. Register the agent in `app.enrichment`. Declare `OUTPUT_KEYS`.
2. Add a flattening-rule function to `FLATTENING_RULES` in
   `app/kg_projection.py` that turns the strategy's `attributes` dict into
   flat `{node_prop: value}` pairs. Keep it strategy-local; don't reach
   into other strategies' outputs.
3. If the property names are open-vocab (like `good_for_*`), extend
   `KEY_PATTERNS` so the drift test covers them.
4. Update `_PROJECTION_STRATEGIES` in `scripts/build_knowledge_graph.py`
   if the orchestrator should feed this strategy's rows into `project()`.

## Related tracked issues

- **#52** — this contract.
- **#60** — LLM confidence scores in `soft_tagger_v1` are uncalibrated.
  Threshold lives in `TAG_CONFIDENCE_THRESHOLD`; revisit when calibration
  data lands.
- **#61** — decide whether `backfill_kg_features.py`'s regex heuristic
  logic migrates into a new enrichment strategy.
- **#38** — long-term per-schema-per-merchant storage direction (tentative).
