#!/bin/bash
# Start all local dev servers for the full stack (backend, frontend, API) in parallel
# Usage: bash start_all_local.sh

# Kill any processes on the target ports first (optional, for clean start)
lsof -ti:3000,8000,8001 | xargs kill -9 2>/dev/null

# Start backend API (assumes Python venv is set up)
echo "Starting backend API on :8000..."
source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null
python -m uvicorn idss.api.server:app --reload --port 8000 &
BACKEND_PID=$!

# Start secondary backend (if needed, e.g., MCP server on 8001)
echo "Starting MCP server on :8001..."
cd mcp-server && python -m app.main --port 8001 &
MCP_PID=$!
cd ..

# Start frontend (assumes Next.js/React/Vite in ../idss-web)
echo "Starting frontend on :3000..."
cd ../idss-web && npm run dev &
FRONTEND_PID=$!
cd ../idss-backend

# Wait for all processes
trap "kill $BACKEND_PID $MCP_PID $FRONTEND_PID" EXIT
wait