"""
slack.py — Slack Events API webhook adapter for the IDSS shopping assistant.

Receives Slack workspace events via HTTP POST (Events API), verifies the
Slack request signature, and bridges messages to the existing process_chat()
function in agent/chat_endpoint.py.

Slack app requirements (api.slack.com/apps):
  - Event Subscriptions enabled, Request URL: https://<host>/channels/slack/events
  - Bot event subscriptions: message.channels, message.im, app_mention
  - OAuth scopes: chat:write, channels:history, im:history, im:write
  - Environment variables: SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET

The handler acknowledges Slack within 3 seconds (as required) and processes
the message in a FastAPI BackgroundTask to avoid timeout.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from channels.formatter import format_for_slack
from channels.session_store import get_or_create

logger = logging.getLogger("channels.slack")

router = APIRouter(tags=["channels"])

# ---------------------------------------------------------------------------
# Slack credentials (loaded at import time; missing = channel disabled)
# ---------------------------------------------------------------------------

_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

# Maximum allowed age of a Slack request timestamp (5 minutes per Slack docs)
_MAX_TIMESTAMP_AGE_SECONDS = 300


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(
    raw_body: bytes,
    timestamp: str,
    signature: str,
) -> bool:
    """Return True if the request signature matches the signing secret."""
    if not _SIGNING_SECRET:
        logger.warning("SLACK_SIGNING_SECRET not set — skipping signature check")
        return True

    # Replay attack protection
    try:
        if abs(time.time() - float(timestamp)) > _MAX_TIMESTAMP_AGE_SECONDS:
            return False
    except (TypeError, ValueError):
        return False

    base = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        _SIGNING_SECRET.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Message processor (runs in background to stay within Slack's 3s ack window)
# ---------------------------------------------------------------------------

async def _process_and_reply(
    text: str,
    user_id: str,
    channel_id: str,
    thread_ts: str | None,
) -> None:
    """Call process_chat() and post the formatted response back to Slack."""
    try:
        from slack_sdk.web.async_client import AsyncWebClient
        from agent import process_chat, ChatRequest

        session_id = await get_or_create("slack", user_id)

        response = await process_chat(ChatRequest(message=text, session_id=session_id))
        blocks = format_for_slack(response)

        client = AsyncWebClient(token=_BOT_TOKEN)
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=blocks,
            # Plain-text fallback for notifications
            text=response.message[:3000] if response.message else "Here you go!",
        )
    except ImportError as exc:
        logger.error(
            "slack-sdk not installed. Run: pip install 'slack-sdk>=3.27.0'. Error: %s", exc
        )
    except Exception:
        logger.exception("Error processing Slack message from user %s", user_id)


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------

@router.post("/slack/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
) -> JSONResponse:
    """
    Slack Events API webhook endpoint.

    Handles:
      - url_verification  → echoes challenge back immediately
      - event_callback    → dispatches message handling as background task
    """
    raw_body = await request.body()

    # Verify Slack signature
    if not _verify_signature(raw_body, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    try:
        payload: Dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("type")

    # ── URL verification challenge (one-time, during Slack app setup) ──────────
    if event_type == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    # ── Event callback ─────────────────────────────────────────────────────────
    if event_type == "event_callback":
        event: Dict[str, Any] = payload.get("event", {})

        # Ignore bot messages (including our own replies) to prevent loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return JSONResponse({"ok": True})

        msg_type = event.get("type", "")
        if msg_type not in ("message", "app_mention"):
            return JSONResponse({"ok": True})

        text: str = event.get("text", "").strip()
        user_id: str = event.get("user", "unknown")
        channel_id: str = event.get("channel", "")
        thread_ts: str | None = event.get("thread_ts") or event.get("ts")

        # Strip bot mention prefix (<@BOTID> text)
        text = _strip_mention(text)

        if not text or not channel_id:
            return JSONResponse({"ok": True})

        if not _BOT_TOKEN:
            logger.error("SLACK_BOT_TOKEN not set — cannot reply to Slack message")
            return JSONResponse({"ok": True})

        # Acknowledge immediately; process in background
        background_tasks.add_task(
            _process_and_reply, text, user_id, channel_id, thread_ts
        )

    return JSONResponse({"ok": True})


def _strip_mention(text: str) -> str:
    """Remove leading <@USERID> mention from app_mention events."""
    return re.sub(r"^<@[A-Z0-9]+>\s*", "", text).strip()
