# merchant-agent

Standalone merchant-agent backend seed.

This repository is carved out from `interactive-decision-support-system/idss-backend`
as the implementation home for the merchant agent and future merchant admin UI.

Initial scope:

- FastAPI merchant/admin/search backend in `apps/backend`
- enrichment and ingestion pipeline
- per-merchant catalog isolation
- contract package placeholder for generated OpenAPI TypeScript types

Out of scope for this seed:

- legacy IDSS paper artifacts
- legacy `/chat` surface
- vehicle domain
- OpenClaw/UCP/ACP compatibility layers
- `idss-web`, which remains a separate repo
