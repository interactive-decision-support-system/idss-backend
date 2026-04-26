# Migration Tip: merchant-agent repo seed

This branch is the staging point for issue #109: creating a fresh
`interactive-decision-support-system/merchant-agent` repo that contains only
the relevant merchant-agent backend code.

This branch is not intended to merge back into `idss-backend`. Its purpose is
to make the proposed seed state reviewable before copying it into a new repo.

## Source

- Source repository: `interactive-decision-support-system/idss-backend`
- Source branch: `enrichment-pipeline-dev`
- Migration branch: `migration/merchant-agent-seed`
- Destination repository: `interactive-decision-support-system/merchant-agent`
- Governing issue: #109
- Included fix: `0e25a16` / `fix/mocklaptops-real-row-seed-124`

## Seed Layout

```text
merchant-agent/
  apps/
    backend/
      merchant_agent/
      scripts/
      tests/
      migrations/
      pyproject.toml
  packages/
    contract/
  pnpm-workspace.yaml
  turbo.json
  justfile
  MIGRATION_SOURCE.md
```

`apps/merchant-web/` is intentionally not created in this first seed. It is a
follow-on admin UI app. `idss-web` remains a separate repository and is not
covered here.

`uv.lock` is not generated in this branch because `uv` is not installed in the
current local environment. Generate it after the standalone repo is created.

## Carried Forward

The seed carries the merchant-agent backend core:

- per-merchant catalog model/binding code
- merchant registry and admin/search contract
- CSV ingestion and catalog normalization
- enrichment orchestration, agents, tracing, metrics, and persistence helpers
- KG projection/service code
- vector search support
- Supabase product store helper
- migrations 002-006 for the current merchant schema
- operational scripts:
  - `bootstrap_merchant.py`
  - `run_enrichment.py`
  - `enrichment_inspector.py`
  - `seed_mock_laptops.py`
  - `run_normalizer.py`
- focused tests for enrichment, merchant registry/admin, KG, vector search,
  enriched reader, catalog binding, and score breakdown behavior

The old `main.py` and `endpoints.py` are not copied verbatim. They are replaced
with a narrower FastAPI surface for:

- `GET /health`
- `POST /api/search-products`
- `POST /merchant`
- `GET /merchant`
- `DELETE /merchant/{merchant_id}`
- `POST /merchant/search`
- `POST /merchant/{merchant_id}/search`
- `GET /merchant/{merchant_id}/health`

## Left Behind

The seed intentionally leaves behind:

- legacy IDSS package and paper/evaluation artifacts
- legacy `/chat` surface
- vehicle-specific infrastructure
- OpenClaw/UCP/ACP compatibility layers
- Slack/channel adapters
- scratch scripts, local DBs, generated run artifacts, images, and old evals
- `idss-web`

## Validation

Validation target before using this as the new repo seed:

```bash
python3 -m compileall apps/backend
PYTHONPATH=apps/backend python3 -c "import merchant_agent.main"
cd apps/backend && python3 -m pytest tests
```

Any remaining failures should be documented in the draft PR body and fixed
before the clean initial commit is made in the new repository.

## Fresh Repo Creation

After this branch is reviewed, export the tree into a clean directory and
create the new repo with a clean initial commit. Keep provenance in
`MIGRATION_SOURCE.md`; do not preserve all historical `idss-backend` commits in
the new product repository.
