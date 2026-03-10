"""
Tests for the Slack channel adapter (channels/slack.py and channels/formatter.py).

These are pure unit tests — no network calls, no DB, no LLM.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── ensure repo root is on path ─────────────────────────────────────────────
import sys
_root = Path(__file__).resolve().parents[3]  # idss-backend/
sys.path.insert(0, str(_root))

from channels.slack import _verify_signature, _strip_mention
from channels.formatter import format_for_slack, _md_to_mrkdwn
from channels.session_store import get_or_create, clear


# ===========================================================================
# Helpers
# ===========================================================================

_TEST_SECRET = "test_signing_secret_abc123"


def _make_signature(body: str, timestamp: str, secret: str = _TEST_SECRET) -> str:
    base = f"v0:{timestamp}:{body}"
    sig = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return f"v0={sig}"


def _now_ts() -> str:
    return str(int(time.time()))


# ===========================================================================
# Signature verification
# ===========================================================================

class TestVerifySignature:
    def test_valid_signature(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", _TEST_SECRET)
        # Re-import to pick up env var
        import importlib, channels.slack as sl
        importlib.reload(sl)

        body = b'{"type":"event_callback"}'
        ts = _now_ts()
        sig = _make_signature(body.decode(), ts)

        assert sl._verify_signature(body, ts, sig) is True

    def test_invalid_signature(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", _TEST_SECRET)
        import importlib, channels.slack as sl
        importlib.reload(sl)

        body = b'{"type":"event_callback"}'
        ts = _now_ts()
        assert sl._verify_signature(body, ts, "v0=badhash") is False

    def test_expired_timestamp(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", _TEST_SECRET)
        import importlib, channels.slack as sl
        importlib.reload(sl)

        body = b'{"type":"event_callback"}'
        old_ts = str(int(time.time()) - 400)  # 400s ago > 300s limit
        sig = _make_signature(body.decode(), old_ts)
        assert sl._verify_signature(body, old_ts, sig) is False

    def test_no_secret_bypasses_check(self, monkeypatch):
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        import importlib, channels.slack as sl
        importlib.reload(sl)

        # When secret is not set, verification is skipped (returns True)
        assert sl._verify_signature(b"body", _now_ts(), "v0=anything") is True


# ===========================================================================
# Strip mention
# ===========================================================================

class TestStripMention:
    def test_strips_app_mention(self):
        assert _strip_mention("<@U12345678> show me laptops") == "show me laptops"

    def test_no_mention_unchanged(self):
        assert _strip_mention("show me laptops") == "show me laptops"

    def test_strips_whitespace(self):
        assert _strip_mention("   ") == ""


# ===========================================================================
# Formatter: markdown → mrkdwn conversion
# ===========================================================================

class TestMdToMrkdwn:
    def test_bold(self):
        assert _md_to_mrkdwn("**hello**") == "*hello*"

    def test_link(self):
        assert _md_to_mrkdwn("[text](http://example.com)") == "<http://example.com|text>"

    def test_strips_html(self):
        assert _md_to_mrkdwn("<b>hello</b>") == "hello"

    def test_double_underscore_bold(self):
        assert _md_to_mrkdwn("__hello__") == "*hello*"


# ===========================================================================
# Formatter: format_for_slack
# ===========================================================================

def _mock_response(response_type: str, **kwargs) -> Dict[str, Any]:
    defaults = {
        "response_type": response_type,
        "message": "Test message",
        "session_id": "sess-123",
        "quick_replies": None,
        "recommendations": None,
        "bucket_labels": None,
        "comparison_data": None,
        "research_data": None,
    }
    defaults.update(kwargs)
    return defaults


class TestFormatForSlack:
    def test_question_produces_section_block(self):
        resp = _mock_response("question", message="What's your budget?", quick_replies=["<$500", "$500-1000", ">$1000"])
        blocks = format_for_slack(resp)

        assert len(blocks) >= 1
        # First block should be a section with the message
        assert blocks[0]["type"] == "section"
        assert "What's your budget?" in blocks[0]["text"]["text"]

    def test_question_with_quick_replies(self):
        resp = _mock_response("question", message="Usage?", quick_replies=["Gaming", "Work"])
        blocks = format_for_slack(resp)
        # At least one block should contain quick reply options
        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks)
        assert "Gaming" in all_text

    def test_recommendations_has_header(self):
        product = {
            "name": "Dell XPS 15",
            "brand": "Dell",
            "price_value": 1299.99,
            "link": "https://dell.com/xps15",
        }
        resp = _mock_response("recommendations", recommendations=[[product]], message="Great choice!")
        blocks = format_for_slack(resp)

        types = [b["type"] for b in blocks]
        assert "header" in types

    def test_recommendations_product_name_in_output(self):
        product = {"name": "MacBook Pro", "brand": "Apple", "price_value": 2499.0}
        resp = _mock_response("recommendations", recommendations=[[product]])
        blocks = format_for_slack(resp)
        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
        assert "MacBook Pro" in all_text

    def test_empty_message_returns_fallback(self):
        resp = _mock_response("question", message="")
        blocks = format_for_slack(resp)
        assert len(blocks) >= 1

    def test_compare_type(self):
        resp = _mock_response("compare", message="Dell wins on performance, Apple on portability.")
        blocks = format_for_slack(resp)
        assert any("Dell wins" in b.get("text", {}).get("text", "") for b in blocks)


# ===========================================================================
# Session store (using asyncio.run() to avoid pytest-asyncio dependency)
# ===========================================================================

import asyncio as _asyncio


class TestSessionStore:
    def test_creates_session(self):
        async def _run():
            await clear("slack")
            session_id = await get_or_create("slack", "U111")
            assert isinstance(session_id, str) and len(session_id) > 0
        _asyncio.run(_run())

    def test_same_user_same_session(self):
        async def _run():
            await clear("slack")
            s1 = await get_or_create("slack", "U222")
            s2 = await get_or_create("slack", "U222")
            assert s1 == s2
        _asyncio.run(_run())

    def test_different_users_different_sessions(self):
        async def _run():
            await clear("slack")
            s1 = await get_or_create("slack", "U333")
            s2 = await get_or_create("slack", "U444")
            assert s1 != s2
        _asyncio.run(_run())

    def test_clear_by_channel(self):
        async def _run():
            await clear()
            await get_or_create("slack", "U555")
            await clear("slack")
            s2 = await get_or_create("slack", "U555")
            assert isinstance(s2, str)
        _asyncio.run(_run())


# ===========================================================================
# Webhook endpoint (FastAPI TestClient)
# ===========================================================================

class TestSlackEventsEndpoint:
    """Integration-style tests using FastAPI's test client (no real Slack/LLM calls)."""

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", _TEST_SECRET)
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

        # Prevent actual process_chat and Slack API calls
        import importlib
        import channels.slack as sl
        importlib.reload(sl)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import importlib
        import channels.slack as sl
        importlib.reload(sl)

        app = FastAPI()
        app.include_router(sl.router, prefix="/channels")
        return TestClient(app)

    def _post_event(self, client, payload: Dict[str, Any]):
        body = json.dumps(payload)
        ts = _now_ts()
        sig = _make_signature(body, ts)
        return client.post(
            "/channels/slack/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )

    def test_url_verification(self, client):
        resp = self._post_event(client, {"type": "url_verification", "challenge": "my_challenge"})
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "my_challenge"

    def test_invalid_signature_rejected(self, client):
        body = json.dumps({"type": "url_verification", "challenge": "x"})
        resp = client.post(
            "/channels/slack/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": _now_ts(),
                "X-Slack-Signature": "v0=badsig",
            },
        )
        assert resp.status_code == 403

    def test_bot_message_ignored(self, client):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "bot_id": "B12345",
                "text": "I am a bot",
                "channel": "C123",
                "user": "U999",
                "ts": "123.456",
            },
        }
        resp = self._post_event(client, payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
