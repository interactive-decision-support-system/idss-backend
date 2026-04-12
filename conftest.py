"""
Root-level pytest configuration.

Adds the repo root and mcp-server/ to sys.path so that:
  - `agent.*`  imports resolve from idss-backend/agent/
  - `app.*`    imports resolve from idss-backend/mcp-server/app/

This is required because agent/chat_endpoint.py imports from `app.*`
and must be able to find mcp-server/app/ regardless of working directory.
"""
import sys
import os

_ROOT = os.path.dirname(__file__)
_MCP = os.path.join(_ROOT, "mcp-server")

# Prepend so our packages take priority over any installed versions
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)
