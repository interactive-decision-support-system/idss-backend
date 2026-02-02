# Frontend removed

The Next.js frontend (and its build output) has been removed from `idss-mcp/`. Use **YongceLi's original UI** and point it at the backend you need:

- **Original IDSS API** (chat, recommend, session): `idss/api/server.py` → run on port 8000.
- **MCP / e‑commerce API**: `idss-mcp/mcp-server/` → run on port 8001 (`uvicorn app.main:app --port 8001`).

Removed from disk: `idss-mcp/.next/` (Next.js build / .js files), `idss-mcp/node_modules/`, `idss-mcp/.git.bak/`. This directory now contains only `mcp-server/` (Python backend) and this note.
