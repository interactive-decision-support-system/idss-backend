"""
Protocol Trace Logger — captures full request/response bodies for MCP, UCP, and ACP.

Writes to separate JSONL files under logs/ at the project root:
  logs/mcp_traces.jsonl
  logs/ucp_traces.jsonl
  logs/acp_traces.jsonl

Each line is a JSON object with:
  {
    "ts": "2026-02-26T00:00:00Z",
    "protocol": "mcp" | "ucp" | "acp",
    "endpoint": "/api/search-products",
    "method": "POST",
    "status_code": 200,
    "duration_ms": 145.3,
    "request_body": { ... },
    "response_body": { ... }
  }

The body size is capped at 64 KB each to avoid runaway log files.
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("mcp.protocol_trace")

# Project root is 3 levels up from this file (app/ → mcp-server/ → root/)
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_MAX_BODY_BYTES = 64 * 1024  # 64 KB cap

# Eagerly open log file handles once at import time so each request
# only does a single write() + flush() with no open() overhead.
_handles: dict[str, object] = {}


def _get_handle(protocol: str):
    """Return (and lazily open) the log file handle for a protocol."""
    if protocol not in _handles:
        _LOG_DIR.mkdir(exist_ok=True)
        path = _LOG_DIR / f"{protocol}_traces.jsonl"
        _handles[protocol] = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
    return _handles[protocol]


def _truncate(body: object, max_bytes: int = _MAX_BODY_BYTES) -> object:
    """Return body as-is if small enough, otherwise return a truncation marker."""
    try:
        raw = json.dumps(body, default=str)
        if len(raw.encode()) <= max_bytes:
            return body
        return {"__truncated": True, "size_bytes": len(raw.encode())}
    except Exception:
        return {"__unserializable": str(type(body))}


def write_trace(
    protocol: str,
    endpoint: str,
    method: str,
    status_code: int,
    duration_ms: float,
    request_body: Optional[object] = None,
    response_body: Optional[object] = None,
) -> None:
    """
    Append one trace entry to logs/{protocol}_traces.jsonl.

    Safe to call from any thread; Python's GIL protects the write().
    Silently ignores all errors so a log failure never breaks a request.
    """
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "protocol": protocol,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 1),
            "request_body": _truncate(request_body) if request_body is not None else None,
            "response_body": _truncate(response_body) if response_body is not None else None,
        }
        line = json.dumps(entry, default=str) + "\n"
        handle = _get_handle(protocol)
        handle.write(line)  # type: ignore[union-attr]
    except Exception as exc:
        logger.debug("protocol_trace write failed: %s", exc)
