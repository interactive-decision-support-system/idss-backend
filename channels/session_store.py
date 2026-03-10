"""
session_store.py — maps channel-specific user IDs to IDSS session IDs.

Each channel user gets a stable IDSS session_id so conversation context
persists across messages. The store is in-memory (resets on server restart);
upgrade to Redis via `cache_client` when persistence is needed.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

_lock = asyncio.Lock()

# Maps (channel_prefix, user_id) → idss_session_id
# e.g. ("slack", "U12345678") → "550e8400-e29b-41d4-a716-446655440000"
_store: dict[tuple[str, str], str] = {}


async def get_or_create(channel: str, user_id: str) -> str:
    """Return the IDSS session_id for this user, creating one if absent."""
    key = (channel, user_id)
    async with _lock:
        if key not in _store:
            _store[key] = str(uuid.uuid4())
        return _store[key]


async def clear(channel: Optional[str] = None) -> None:
    """Clear sessions (optionally for a single channel). Useful in tests."""
    async with _lock:
        if channel is None:
            _store.clear()
        else:
            for k in list(_store.keys()):
                if k[0] == channel:
                    del _store[k]
