#!/bin/bash
# Start all local dev servers for the full stack (MCP backend + frontend) in parallel.
# Expects to be run from this repo (idss-backend). Frontend is assumed at ../idss-web.
# Usage: ./start_all_local.sh   (from anywhere)

BACKEND_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$BACKEND_ROOT" || exit 1

# Kill any processes on the target ports first (for clean start)
lsof -ti:3000,8001 | xargs kill -9 2>/dev/null

# Activate Python venv
source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null

# Start MCP server (handles all domains: vehicles via Supabase, laptops/books via PostgreSQL)
echo "Starting MCP server on :8001..."
uvicorn app.main:app --app-dir "$BACKEND_ROOT/mcp-server" --reload --port 8001 &
MCP_PID=$!

# Start frontend (Next.js in sibling ../idss-web)
FRONTEND_DIR="$BACKEND_ROOT/../idss-web"
if [ -d "$FRONTEND_DIR" ]; then
  echo "Starting frontend on :3000..."
  (cd "$FRONTEND_DIR" && npm run dev) &
  FRONTEND_PID=$!
else
  echo "Warning: frontend dir not found at $FRONTEND_DIR; skipping."
  FRONTEND_PID=
fi

echo "MCP backend PID: $MCP_PID (port 8001)"
echo "Frontend PID:    $FRONTEND_PID (port 3000)"

trap "kill $MCP_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
