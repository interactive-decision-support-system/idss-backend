"""
OpenClaw Integration Tests

Covers all OpenClaw-related components introduced to make the skill work
end-to-end from any messaging app (WhatsApp, Telegram, Discord, etc.):

  Layer 1 — _format_response_as_text   (pure unit, no I/O)
  Layer 2 — _verify_openclaw_signature (pure unit, no I/O)
  Layer 3 — GET  /search/ebay          (mocked httpx, no real network)
  Layer 4 — POST /chat-text            (mocked process_chat)
  Layer 5 — POST /openclaw/message     (mocked process_chat + HMAC auth)
"""

import os
import sys
import json
import hmac
import hashlib

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ── path + dotenv must come first ─────────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv(override=True)

os.environ.setdefault("MCP_SKIP_PRELOAD", "1")   # skip heavy model preload

# ── App imports ───────────────────────────────────────────────────────────────
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app, _format_response_as_text, _verify_openclaw_signature
from app.database import get_db

# ── In-memory SQLite for DB dependency ───────────────────────────────────────
_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
_Session = sessionmaker(bind=_engine)


def _override_db():
    db = _Session()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_chat_response(**kwargs):
    """Build a minimal ChatResponse for mocking process_chat."""
    from agent import ChatResponse
    return ChatResponse(
        message=kwargs.get("message", ""),
        session_id=kwargs.get("session_id", "test-session"),
        response_type=kwargs.get("response_type", "chat"),
        recommendations=kwargs.get("recommendations", None),
        quick_replies=kwargs.get("quick_replies", None),
    )


def _sign_body(body: bytes, secret: str) -> str:
    """Produce the sha256=<hex> signature that OpenClaw would send."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ============================================================================
# Layer 1 — _format_response_as_text
# ============================================================================

class TestFormatResponseAsText:

    def test_empty_response_returns_empty_string(self):
        resp = _make_chat_response()
        assert _format_response_as_text(resp) == ""

    def test_message_only(self):
        resp = _make_chat_response(message="What are you looking for?")
        result = _format_response_as_text(resp)
        assert "What are you looking for?" in result

    def test_recommendations_header_present(self):
        recs = [[{"name": "Dell XPS 15", "price": 1299.0, "rating": 4.5}]]
        resp = _make_chat_response(message="Here are picks:", recommendations=recs)
        result = _format_response_as_text(resp)
        assert "📦 *Top Picks:*" in result

    def test_product_name_and_price_in_output(self):
        recs = [[{"name": "Dell XPS 15", "price": 1299.0}]]
        resp = _make_chat_response(recommendations=recs)
        result = _format_response_as_text(resp)
        assert "Dell XPS 15" in result
        assert "$1,299" in result

    def test_product_rating_shown_when_present(self):
        recs = [[{"name": "ThinkPad X1", "price": 1500.0, "rating": 4.8}]]
        resp = _make_chat_response(recommendations=recs)
        result = _format_response_as_text(resp)
        assert "⭐ 4.8" in result

    def test_no_rating_line_when_absent(self):
        recs = [[{"name": "Laptop A", "price": 500.0}]]
        resp = _make_chat_response(recommendations=recs)
        result = _format_response_as_text(resp)
        assert "⭐" not in result

    def test_recommendations_capped_at_5(self):
        recs = [[{"name": f"Laptop {i}", "price": float(i * 100)} for i in range(10)]]
        resp = _make_chat_response(recommendations=recs)
        result = _format_response_as_text(resp)
        assert "5." in result
        assert "6." not in result

    def test_nested_bucket_recommendations_flattened(self):
        """recommendations is a list-of-lists; all buckets should appear."""
        recs = [
            [{"name": "Laptop A", "price": 800.0}],
            [{"name": "Laptop B", "price": 1200.0}],
        ]
        resp = _make_chat_response(recommendations=recs)
        result = _format_response_as_text(resp)
        assert "Laptop A" in result
        assert "Laptop B" in result

    def test_cpu_brand_prefix_stripped(self):
        recs = [[{"name": "Test", "laptop": {
            "specs": {"processor": "Intel Core i9-13900H", "ram": "32GB", "storage": "1TB"}
        }}]]
        resp = _make_chat_response(recommendations=recs)
        result = _format_response_as_text(resp)
        assert "Intel Core" not in result   # prefix stripped
        assert "i9-13900H" in result

    def test_quick_replies_section_header(self):
        resp = _make_chat_response(
            message="Done!",
            quick_replies=["Tell me more", "Compare models", "Check eBay"],
        )
        result = _format_response_as_text(resp)
        assert "💬 *You can ask:*" in result
        assert "• Tell me more" in result
        assert "• Compare models" in result
        assert "• Check eBay" in result

    def test_quick_replies_capped_at_3(self):
        resp = _make_chat_response(quick_replies=[f"Q{i}" for i in range(6)])
        result = _format_response_as_text(resp)
        assert "• Q0" in result
        assert "• Q3" not in result


# ============================================================================
# Layer 2 — _verify_openclaw_signature
# ============================================================================

class TestVerifyOpenClawSignature:

    def _clear_secret(self):
        """Helper: return env dict without OPENCLAW_WEBHOOK_SECRET."""
        return {k: v for k, v in os.environ.items() if k != "OPENCLAW_WEBHOOK_SECRET"}

    def test_no_secret_configured_always_passes(self):
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            assert _verify_openclaw_signature(b"any body", None) is True
            assert _verify_openclaw_signature(b"any body", "sha256=badhex") is True

    def test_valid_signature_passes(self):
        secret = "test-secret-key"
        body = b'{"message":"hello"}'
        sig = _sign_body(body, secret)
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": secret}):
            assert _verify_openclaw_signature(body, sig) is True

    def test_wrong_secret_fails(self):
        body = b'{"message":"hello"}'
        sig = _sign_body(body, "correct-secret")
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "wrong-secret"}):
            assert _verify_openclaw_signature(body, sig) is False

    def test_tampered_body_fails(self):
        secret = "my-secret"
        original = b'{"message":"original"}'
        tampered = b'{"message":"tampered"}'
        sig = _sign_body(original, secret)
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": secret}):
            assert _verify_openclaw_signature(tampered, sig) is False

    def test_missing_header_fails_when_secret_set(self):
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "secret"}):
            assert _verify_openclaw_signature(b"body", None) is False

    def test_wrong_algorithm_prefix_fails(self):
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "secret"}):
            assert _verify_openclaw_signature(b"body", "md5=abc123") is False

    def test_malformed_header_no_equals_fails(self):
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "secret"}):
            assert _verify_openclaw_signature(b"body", "noequalssign") is False

    def test_compare_digest_prevents_timing_attacks(self):
        """_verify_openclaw_signature uses hmac.compare_digest, not ==."""
        # This is an indirect check: give a sig of all-zeros → should fail safely
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "secret"}):
            assert _verify_openclaw_signature(b"body", "sha256=" + "0" * 64) is False


# ============================================================================
# Layer 3 — GET /search/ebay
# ============================================================================

class TestSearchEbay:

    def setup_method(self):
        self.client = TestClient(app)
        # Ensure no real API key leaks into these unit tests
        os.environ.pop("EBAY_APP_ID", None)

    # ── url_only fallback (RSS fails) ─────────────────────────────────────────

    def test_url_only_when_rss_fails(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get.side_effect = Exception("network err")
            resp = self.client.get("/search/ebay", params={"q": "dell laptop"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "url_only"
        assert data["results"] == []

    def test_search_url_contains_encoded_query(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get.side_effect = Exception("fail")
            resp = self.client.get("/search/ebay", params={"q": "MacBook Pro"})
        url = resp.json()["search_url"]
        assert "MacBook" in url

    def test_max_price_reflected_in_response_and_url(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get.side_effect = Exception("fail")
            resp = self.client.get("/search/ebay", params={"q": "laptop", "max_price": 1000})
        data = resp.json()
        assert data["max_price"] == 1000.0
        assert "_udhi=1000" in data["search_url"]

    def test_query_echoed_in_response(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get.side_effect = Exception("fail")
            resp = self.client.get("/search/ebay", params={"q": "ThinkPad X1"})
        assert resp.json()["query"] == "ThinkPad X1"

    # ── RSS fallback path ─────────────────────────────────────────────────────

    def test_rss_fallback_returns_results(self):
        rss_xml = (
            '<?xml version="1.0"?><rss><channel>'
            '<item>'
            '<title>Dell XPS 15 9500</title>'
            '<link>https://www.ebay.com/itm/123456789</link>'
            '<description>Excellent condition. $999.99 Buy It Now. Free shipping.</description>'
            '</item>'
            '</channel></rss>'
        )
        mock_resp = MagicMock()
        mock_resp.text = rss_xml
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get.return_value = mock_resp
            resp = self.client.get("/search/ebay", params={"q": "dell xps"})
        data = resp.json()
        assert data["source"] == "rss"
        assert len(data["results"]) == 1
        assert data["results"][0]["title"] == "Dell XPS 15 9500"
        assert data["results"][0]["price"] == "$999.99"
        assert data["results"][0]["url"] == "https://www.ebay.com/itm/123456789"

    def test_rss_result_without_price_has_none_price(self):
        rss_xml = (
            '<?xml version="1.0"?><rss><channel>'
            '<item>'
            '<title>Some Laptop</title>'
            '<link>https://www.ebay.com/itm/999</link>'
            '<description>No price mentioned here.</description>'
            '</item>'
            '</channel></rss>'
        )
        mock_resp = MagicMock()
        mock_resp.text = rss_xml
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get.return_value = mock_resp
            resp = self.client.get("/search/ebay", params={"q": "laptop"})
        assert resp.json()["results"][0]["price"] is None


# ============================================================================
# Layer 4 — POST /chat-text
# ============================================================================

class TestChatText:

    def setup_method(self):
        self.client = TestClient(app)

    def test_returns_200_with_text_and_session_id(self):
        mock_resp = _make_chat_response(
            message="I found some laptops!",
            session_id="ses-abc",
            response_type="recommendations",
        )
        with patch("app.main.process_chat", AsyncMock(return_value=mock_resp)):
            resp = self.client.post(
                "/chat-text",
                json={"message": "find me a laptop", "session_id": "ses-abc"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "I found some laptops!" in data["text"]
        assert data["session_id"] == "ses-abc"
        assert "response_type" in data

    def test_session_id_from_process_chat_is_returned(self):
        mock_resp = _make_chat_response(message="Hi!", session_id="auto-gen-id")
        with patch("app.main.process_chat", AsyncMock(return_value=mock_resp)):
            resp = self.client.post("/chat-text", json={"message": "hello"})
        assert resp.json()["session_id"] == "auto-gen-id"

    def test_recommendations_formatted_in_text(self):
        mock_resp = _make_chat_response(
            message="Top picks:",
            session_id="s1",
            response_type="recommendations",
            recommendations=[[{"name": "ThinkPad X1 Carbon", "price": 1499.0, "rating": 4.9}]],
        )
        with patch("app.main.process_chat", AsyncMock(return_value=mock_resp)):
            resp = self.client.post("/chat-text", json={"message": "work laptop", "session_id": "s1"})
        data = resp.json()
        assert "ThinkPad X1 Carbon" in data["text"]
        assert "📦 *Top Picks:*" in data["text"]

    def test_quick_replies_in_text(self):
        mock_resp = _make_chat_response(
            message="Here you go!",
            session_id="s2",
            quick_replies=["Tell me more", "Compare"],
        )
        with patch("app.main.process_chat", AsyncMock(return_value=mock_resp)):
            resp = self.client.post("/chat-text", json={"message": "laptop", "session_id": "s2"})
        assert "Tell me more" in resp.json()["text"]

    def test_response_type_field_present(self):
        mock_resp = _make_chat_response(response_type="question", session_id="s3")
        with patch("app.main.process_chat", AsyncMock(return_value=mock_resp)):
            resp = self.client.post("/chat-text", json={"message": "hi", "session_id": "s3"})
        assert resp.json()["response_type"] == "question"

    def test_missing_message_field_returns_422(self):
        resp = self.client.post("/chat-text", json={"session_id": "s4"})
        assert resp.status_code == 422


# ============================================================================
# Layer 5 — POST /openclaw/message
# ============================================================================

class TestOpenClawMessageWebhook:

    def setup_method(self):
        self.client = TestClient(app)
        self.mock_resp = _make_chat_response(
            message="Great laptops found!",
            session_id="oc-12345",
            response_type="recommendations",
            recommendations=[[{"name": "HP Spectre x360", "price": 1299.0}]],
        )

    def _clear_secret(self):
        return {k: v for k, v in os.environ.items() if k != "OPENCLAW_WEBHOOK_SECRET"}

    def _post(self, payload: dict, secret: str | None = None, sig_override: str | None = None):
        body = json.dumps(payload).encode()
        headers: dict = {"Content-Type": "application/json"}
        if secret:
            headers["X-OpenClaw-Signature"] = sig_override or _sign_body(body, secret)
        elif sig_override:
            headers["X-OpenClaw-Signature"] = sig_override
        return self.client.post("/openclaw/message", content=body, headers=headers)

    # ── Happy paths ───────────────────────────────────────────────────────────

    def test_no_secret_accepts_without_signature(self):
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            with patch("app.main.process_chat", AsyncMock(return_value=self.mock_resp)):
                resp = self._post({"message": "find me a laptop", "session_id": "oc-12345"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "oc-12345"
        assert isinstance(data["text"], str) and len(data["text"]) > 0

    def test_valid_signature_accepted(self):
        secret = "valid-webhook-secret"
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": secret}):
            with patch("app.main.process_chat", AsyncMock(return_value=self.mock_resp)):
                resp = self._post({"message": "gaming laptop"}, secret=secret)
        assert resp.status_code == 200

    def test_response_contains_formatted_recommendations(self):
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            with patch("app.main.process_chat", AsyncMock(return_value=self.mock_resp)):
                resp = self._post({"message": "show me laptops", "session_id": "oc-test"})
        data = resp.json()
        assert "HP Spectre x360" in data["text"]
        assert "📦 *Top Picks:*" in data["text"]

    def test_session_id_auto_generated_when_absent(self):
        auto_resp = _make_chat_response(message="Hello!", session_id="oc-auto-abc")
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            with patch("app.main.process_chat", AsyncMock(return_value=auto_resp)):
                resp = self._post({"message": "hi"})
        assert resp.status_code == 200
        # session_id comes back from process_chat's response
        assert resp.json()["session_id"] == "oc-auto-abc"

    def test_platform_and_user_id_fields_accepted(self):
        """platform and user_id are optional metadata; must not cause errors."""
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            with patch("app.main.process_chat", AsyncMock(return_value=self.mock_resp)):
                resp = self._post({
                    "message": "laptop under $800",
                    "session_id": "oc-wa",
                    "platform": "whatsapp",
                    "user_id": "user-42",
                })
        assert resp.status_code == 200

    # ── Auth failure paths ────────────────────────────────────────────────────

    def test_invalid_signature_returns_401(self):
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "correct-secret"}):
            resp = self._post({"message": "laptop"}, sig_override="sha256=deadbeefdeadbeef")
        assert resp.status_code == 401

    def test_missing_signature_returns_401_when_secret_set(self):
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "my-secret"}):
            resp = self._post({"message": "laptop"})  # no signature header
        assert resp.status_code == 401

    def test_wrong_algorithm_prefix_returns_401(self):
        body = json.dumps({"message": "laptop"}).encode()
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": "secret"}):
            resp = self.client.post(
                "/openclaw/message",
                content=body,
                headers={"Content-Type": "application/json", "X-OpenClaw-Signature": "md5=abc123"},
            )
        assert resp.status_code == 401

    def test_tampered_body_rejected(self):
        secret = "strict-secret"
        original = json.dumps({"message": "original"}).encode()
        sig = _sign_body(original, secret)
        tampered = json.dumps({"message": "tampered"}).encode()
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_SECRET": secret}):
            resp = self.client.post(
                "/openclaw/message",
                content=tampered,
                headers={"Content-Type": "application/json", "X-OpenClaw-Signature": sig},
            )
        assert resp.status_code == 401

    # ── Malformed payload ─────────────────────────────────────────────────────

    def test_invalid_json_returns_422(self):
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            resp = self.client.post(
                "/openclaw/message",
                content=b"not-valid-json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 422

    def test_missing_message_field_returns_422(self):
        with patch.dict(os.environ, self._clear_secret(), clear=True):
            resp = self.client.post(
                "/openclaw/message",
                json={"session_id": "s1"},   # no "message" key
            )
        assert resp.status_code == 422
