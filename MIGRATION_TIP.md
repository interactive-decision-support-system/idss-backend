# Migration Tip: merchant-agent repo seed

This branch is the staging point for issue #109: creating a fresh
`interactive-decision-support-system/merchant-agent` repo that contains only
the relevant merchant-agent code.

This branch is not intended to merge back into `idss-backend`. Its purpose is
to make the intended seed state reviewable before copying it into a new repo.

## Source

- Source repository: `interactive-decision-support-system/idss-backend`
- Source branch: `enrichment-pipeline-dev`
- Migration branch: `migration/merchant-agent-seed`
- Destination repository: `interactive-decision-support-system/merchant-agent`
- Governing issue: #109

## Outcome

Create a new repo with a clean initial history and only the code needed for the
merchant-agent product. The migration should use a whitelist approach: carry
forward only the files that are intentionally part of the new product, rather
than deleting legacy files in place.

The new repo should start in this shape:

```text
merchant-agent/
  apps/
    backend/
      merchant_agent/
      agent/
      scripts/
      tests/
      migrations/
      pyproject.toml
      uv.lock
  packages/
    contract/
  pnpm-workspace.yaml
  turbo.json
  justfile
  MIGRATION_SOURCE.md
```

`apps/merchant-web/` is intentionally not created in this first seed. It is the
follow-on admin UI app described in #109.

`idss-web` remains separate and is not covered by this migration branch.

## Carry Forward

Carry forward the merchant-agent backend core:

- `mcp-server/app/merchant_agent.py`
- `mcp-server/app/merchant.py`
- `mcp-server/app/contract.py`
- `mcp-server/app/kg_service.py`
- `mcp-server/app/kg_projection.py`
- `mcp-server/app/vector_search.py`
- `mcp-server/app/enriched_reader.py`
- `mcp-server/app/catalog.py`
- `mcp-server/app/catalog_ingestion.py`
- `mcp-server/app/csv_importer.py`
- `mcp-server/app/enrichment/`
- `mcp-server/app/ingestion/`
- `mcp-server/app/tools/supabase_product_store.py`
- relevant merchant/admin routes from `mcp-server/app/main.py`
- relevant compatibility routes from `mcp-server/app/endpoints.py`, only if they
  are still required by the merchant-agent backend contract

Carry forward the migrations that belong to the merchant-agent storage model:

- `mcp-server/migrations/002*`
- `mcp-server/migrations/003*`
- `mcp-server/migrations/004*`
- `mcp-server/migrations/005*`
- `mcp-server/migrations/006*`

Carry forward operational scripts that are still part of the merchant-agent
workflow:

- `scripts/bootstrap_merchant.py`
- `scripts/run_enrichment.py`
- `scripts/enrichment_inspector.py`
- `scripts/seed_mock_laptops.py`
- `scripts/run_normalizer.py`

Carry forward tests only where they exercise the retained backend surface:

- merchant registry/admin HTTP tests
- enrichment pipeline tests
- ingestion tests
- KG projection/service tests
- vector search tests
- enriched reader tests
- catalog binding tests
- offer score breakdown tests
- per-merchant vector index tests

Carry forward documentation only after rewriting it for the new repo:

- `ARCHITECTURE.md`
- `CLAUDE.md`

Do not carry these docs verbatim if they still describe the old two-repo
workspace, paper-era IDSS scaffolding, or legacy `/chat` surface.

## Leave Behind

Leave behind all code that belongs to the historical IDSS project, paper-era
experiments, vehicle domain, or legacy shopping chat implementation.

Leave behind entirely:

- `idss/`
- `api/`
- `channels/`
- `openclaw-skill/`
- `evaluation/`
- `testing/`
- `knowledge_base/`
- root-level paper/evaluation artifacts such as `newacmecsyspaper.tex`
- root-level generated latency logs and images
- orphaned SQLite databases
- historical audit markdowns that are not rewritten for the new repo

Leave behind the legacy chat/shopping-agent surface unless a specific piece is
promoted into the new backend contract:

- `agent/chat_endpoint.py`
- `agent/comparison_agent.py`
- `agent/query_rewriter.py`
- legacy branches inside `agent/universal_agent.py`
- legacy interview glue unless it is still required by the new agent contract
- `mcp-server/app/research_compare.py`
- `mcp-server/app/laptop_recommender.py`
- `mcp-server/app/complex_query.py`
- `mcp-server/app/conversation_controller.py`
- `mcp-server/app/blackbox_api.py`
- `mcp-server/app/knowledge_graph.py`
- `mcp-server/app/rca_analyzer.py`
- `mcp-server/app/llm_validator.py`
- `mcp-server/app/or_filter_parser.py`
- `mcp-server/app/custom_genre_handler.py`
- `mcp-server/app/interview_flow_handler.py`

Leave behind the vehicle stack:

- vehicle data stores
- vehicle search tools
- vehicle-specific cards or schemas
- vehicle branches in shared agents or endpoints

Leave behind `mcp-server/scripts/` by default. Promote individual scripts only
after proving they are part of the retained merchant-agent workflow.

Leave behind `mcp-server/` top-level scratch scripts, benchmark scripts, local
database files, generated JSON files, generated images, stale design notes, and
root-level `test_*.py` files that do not exercise retained code.

## Validation

Before using this branch as the seed for the new repo, validate the carried
tree with:

```bash
pytest
python -m compileall apps/backend
python apps/backend/scripts/run_enrichment.py --help
python apps/backend/scripts/bootstrap_merchant.py --help
python apps/backend/scripts/enrichment_inspector.py --help
```

Also scan for stale references:

```bash
rg "from idss|import idss|mcp-server|vehicle|chat_endpoint|research_compare" apps/backend
```

The seed should not depend on the old repository layout, the legacy `idss`
package, the vehicle domain, or the historical `/chat` implementation.

## Draft PR Expectations

The draft PR from this branch should state clearly:

- it is not intended to merge into `idss-backend`
- it is the proposed seed/tip for the fresh `merchant-agent` repo
- it closes no issue by itself
- it implements the migration direction from #109
- it lists validation commands and their results
- it calls out any intentionally deferred pieces

## Fresh Repo Creation

After the seed tree is reviewed, create the fresh repo from a clean exported
directory rather than preserving all historical `idss-backend` commits.

The new repo should have a clean initial commit plus a provenance file:

```text
MIGRATION_SOURCE.md
```

That file should record:

- source repo
- source branch
- source commit SHA
- migration issue
- migration branch or PR URL
- date of repo creation

This keeps the new repository's history focused on the new product while still
preserving traceability to the old source.
