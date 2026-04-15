"""
MCP E-commerce Server - Main FastAPI Application

Exposes typed tool-call endpoints for agent interactions.
All endpoints follow the standard response envelope pattern.
"""

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text as _sa_text
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import asyncio
import uvicorn
import logging
import traceback
import os
import sys
import time as _time
import json as _json
import uuid as _uuid
from datetime import datetime as _datetime
from dotenv import load_dotenv
from pathlib import Path
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

# Add repo root to Python path so `agent` package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger("mcp.main")

# Load environment variables from .env file
# Look for .env in project root (parent of mcp-server)
# Path: app/main.py -> app -> mcp-server -> root/.env
# This makes OPENAI_API_KEY and other env vars available to all modules
env_path = Path(__file__).parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)  # Explicitly specify path to .env file
else:
    # Fallback: try default locations
    load_dotenv()

from app.database import get_db, engine, Base
from app.schemas import (
    SearchProductsRequest, SearchProductsResponse,
    GetProductRequest, GetProductResponse,
    AddToCartRequest, AddToCartResponse,
    CheckoutRequest, CheckoutResponse
)
from app.endpoints import search_products, get_product, add_to_cart, checkout
from app.cache import cache_client
from app.metrics import metrics_collector
from app.tool_schemas import get_all_tools_for_provider, ALL_TOOLS
from app.merchant_feed import export_feed
from app.idss_adapter import (
    search_products_idss, get_product_universal,
    search_products_universal
)
from app.ucp_schemas import (
    UCPSearchRequest, UCPSearchResponse,
    UCPGetProductRequest, UCPGetProductResponse,
    UCPAddToCartRequest, UCPAddToCartResponse,
    UCPCheckoutRequest, UCPCheckoutResponse,
    UCPGetCartRequest, UCPGetCartResponse,
    UCPRemoveFromCartRequest, UCPRemoveFromCartResponse,
    UCPUpdateCartRequest, UCPUpdateCartResponse,
)
from app.ucp_endpoints import (
    ucp_search, ucp_get_product, ucp_add_to_cart, ucp_checkout,
    ucp_get_cart, ucp_remove_from_cart, ucp_update_cart,
)
from app.ucp_event_logger import log_ucp_event
from app.cart_action_agent import (
    build_ucp_get_cart,
    build_ucp_add_to_cart,
    build_ucp_remove_from_cart,
    build_ucp_checkout,
    build_ucp_update_cart,
    build_acp_create_session,
    build_acp_update_session,
    build_acp_complete_session,
)
from app.acp_schemas import (
    ACPCreateSessionRequest, ACPUpdateSessionRequest, ACPCompleteSessionRequest,
    ACPCheckoutSession,
)
from app.acp_endpoints import (
    acp_create_checkout_session as _acp_create,
    acp_get_checkout_session as _acp_get,
    acp_update_checkout_session as _acp_update,
    acp_complete_checkout_session as _acp_complete,
    acp_cancel_checkout_session as _acp_cancel,
    generate_product_feed,
)
from app.protocol_config import is_acp
from app.supabase_cart import get_supabase_cart_client
from app.supplier_api import router as supplier_router
from app.shipping_tax import calculate_shipping, ALL_STATES
from app.coupons import validate_coupon
from agent import ChatRequest, ChatResponse, process_chat
from agent.interview.session_manager import SessionResponse, ResetRequest, ResetResponse
from agent.interview.session_manager import get_session_state, reset_session, delete_session, list_sessions
try:
    from channels.slack import router as _slack_router
    _slack_router_loaded = True
except ImportError:
    _slack_router = None
    _slack_router_loaded = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler to preload IDSS components.

    Replaces deprecated startup events.
    """
    # Create database tables if they don't exist
    # In production, use Alembic migrations instead
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as _e:
        logger.warning(
            "Could not run Base.metadata.create_all: %s. "
            "Tables should already exist (Supabase / remote DB).",
            _e,
        )

    # Ensure shared_chats table exists (created via raw SQL so no ORM model needed)
    try:
        with engine.connect() as _conn:
            _conn.execute(_sa_text("""
                CREATE TABLE IF NOT EXISTS shared_chats (
                    share_id VARCHAR(8) PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    messages JSONB NOT NULL DEFAULT '[]',
                    session_id TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            _conn.commit()
        logger.info("shared_chats table ready")
    except Exception as _e:
        logger.warning("Could not create shared_chats table: %s", _e)

    skip_preload = os.getenv("MCP_SKIP_PRELOAD", "0") == "1"
    if skip_preload:
        logger.info("Skipping IDSS preload (MCP_SKIP_PRELOAD=1)")
        yield
        return

    logger.info("Starting IDSS component preload...")

    try:
        from app.tools.vehicle_search import preload_idss_components
        preload_idss_components()
        logger.info("IDSS components preloaded successfully")
    except Exception as e:
        logger.warning(f"Failed to preload IDSS components: {e}")
        logger.warning("Vehicle search will lazy-load on first request")

    yield

# Initialize FastAPI application
app = FastAPI(
    title="MCP E-commerce Server",
    description=
"Model Context Protocol e-commerce server with typed tool-call endpoints",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for development
# In production, configure this more strictly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Latency logging middleware
# Logs every non-OPTIONS request with method, path, status, and duration_ms.
# Lines are also written to latency_log.jsonl in the project root for offline
# analysis and the poster latency table.
# ---------------------------------------------------------------------------
_LATENCY_LOG_PATH = Path(__file__).parent.parent.parent / "backend_latency_logs.jsonl"

class LatencyLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next) -> StarletteResponse:
        if request.method == "OPTIONS":
            return await call_next(request)
        t0 = _time.perf_counter()
        response = await call_next(request)
        duration_ms = round((_time.perf_counter() - t0) * 1000, 1)
        path = request.url.path
        entry = {
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        }
        # Categorize for poster table
        if "/search-products" in path or "/ucp/search" in path:
            entry["category"] = "feature_search"
        elif "/get-product" in path or "/ucp/product" in path:
            entry["category"] = "get_product"
        elif "/complex-query" in path or "best-value" in path or "similar" in path:
            entry["category"] = "complex_query"
        elif "inventory" in path or "fetch-cart" in path:
            entry["category"] = "inventory"
        elif "/api/action/chat" in path:
            entry["category"] = "agent_chat"
        else:
            entry["category"] = "other"
        logger.info(
            "[LATENCY] %s %s -> %d  %.1fms  [%s]",
            entry["method"], path, entry["status"], duration_ms, entry["category"]
        )
        try:
            with open(_LATENCY_LOG_PATH, "a") as f:
                f.write(_json.dumps(entry) + "\n")
        except Exception:
            pass
        return response

app.add_middleware(LatencyLoggingMiddleware)


class ProtocolHeaderMiddleware(BaseHTTPMiddleware):
    """Add X-Commerce-Protocol header so agents can detect which protocol handled the request."""
    async def dispatch(self, request: StarletteRequest, call_next) -> StarletteResponse:
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/acp/"):
            response.headers["X-Commerce-Protocol"] = "acp"
            # Echo ACP spec headers back so agents can correlate requests
            if "api-version" in request.headers:
                response.headers["API-Version"] = request.headers["api-version"]
            if "idempotency-key" in request.headers:
                response.headers["Idempotency-Key"] = request.headers["idempotency-key"]
        elif path.startswith("/ucp/"):
            response.headers["X-Commerce-Protocol"] = "ucp"
        return response


app.add_middleware(ProtocolHeaderMiddleware)

# Include supplier API router
app.include_router(supplier_router)

# Multi-channel adapters (Slack, etc.)
if _slack_router_loaded and _slack_router is not None:
    app.include_router(_slack_router, prefix="/channels")



@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log unhandled exceptions and return 500 so 'Internal server error' is debuggable."""
    err_msg = str(exc)
    tb = traceback.format_exc()
    logger.error("Unhandled exception: %s\n%s", err_msg, tb)
    # In development, include error detail in response to help debug
    is_dev = os.getenv("ENV", "development").lower() in ("development", "dev", "")
    detail = err_msg if is_dev else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "type": type(exc).__name__},
    )


# 
# Health Check Endpoints
# 

@app.get("/")
def root():
    """
    Root endpoint - basic health check.
    """
    return {
        "service": "MCP E-commerce Server",
        "version": "1.0.0",
        "status": "operational"
    }


@app.get("/health")
def health_check():
    """
    Detailed health check including database and cache connectivity.
    """
    health_status = {
        "service": "healthy",
        "database": "unknown",
        "cache": "unknown"
    }
    
    # Check database connectivity
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db.close()
        health_status["database"] = "healthy"
    except Exception as e:
        health_status["database"] = f"unhealthy: {str(e)}"
        health_status["service"] = "degraded"
    
    # Check Redis connectivity
    try:
        if cache_client.ping():
            health_status["cache"] = "healthy"
        else:
            health_status["cache"] = "unhealthy: no response"
            health_status["service"] = "degraded"
    except Exception as e:
        health_status["cache"] = f"unhealthy: {str(e)}"
        health_status["service"] = "degraded"
    
    return health_status


@app.get("/metrics")
def get_metrics():
    """
    Observability metrics endpoint.

    Returns:
    - Latency percentiles (p50, p95, p99) per endpoint
    - Cache hit rate
    - Request counts and error rates
    - Uptime

    For research and performance analysis.
    """
    return metrics_collector.get_summary()


#
# Chat Endpoint (IDSS-compatible)
#

def _log_conversation_turn(session_id: str, user_msg: str, response: ChatResponse) -> None:
    """Append one conversation turn to logs/conversations.jsonl (fire-and-forget)."""
    try:
        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        entry = {
            "ts": _datetime.utcnow().isoformat() + "Z",
            "session_id": session_id,
            "domain": response.domain,
            "user": user_msg[:500],
            "assistant_type": response.response_type,
            "assistant": response.message[:500] if response.message else "",
            "filters": response.filters,
            "question_count": response.question_count,
        }
        with open(log_dir / "conversations.jsonl", "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass  # logging must never break the response


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Main conversation endpoint - compatible with IDSS /chat API.

    Provides multi-domain support:
    - Vehicles: MCP vehicle_search tool (Supabase)
    - Laptops/Books: MCP interview system

    Request format matches IDSS /chat for frontend compatibility.

    Config overrides (optional):
    - k: Number of interview questions (0 = skip interview)
    - method: 'embedding_similarity' or 'coverage_risk' (vehicles only)
    - n_rows: Number of result rows
    - n_per_row: Items per row
    """
    response = await process_chat(request)
    _log_conversation_turn(response.session_id, request.message, response)
    return response


# ─── Text-only chat endpoint (for messaging apps via OpenClaw) ────────────────

def _format_response_as_text(response: ChatResponse) -> str:
    """
    Convert a ChatResponse to a WhatsApp / iMessage / Telegram friendly
    plain-text string.  No HTML, no markdown beyond *bold* (which WhatsApp
    renders natively).
    """
    import re as _re

    lines: list[str] = []

    # ── Main message ──────────────────────────────────────────────────────────
    if response.message:
        lines.append(response.message)

    # ── Product recommendations ───────────────────────────────────────────────
    if response.recommendations:
        products = [p for bucket in response.recommendations for p in bucket]
        if products:
            lines.append("")
            lines.append("📦 *Top Picks:*")

            for i, p in enumerate(products[:5], 1):
                name = p.get("name") or p.get("title") or "Unknown"
                price = p.get("price")
                price_str = f"${float(price):,.0f}" if price else ""

                # Compact spec pills (processor · RAM · Storage)
                specs: list[str] = []
                laptop = p.get("laptop") or {}
                spec_obj = laptop.get("specs") or {}
                cpu = spec_obj.get("processor", "")
                if cpu:
                    cpu = _re.sub(r"^(Intel Core |AMD |Apple )", "", cpu, flags=_re.I).strip()
                    if len(cpu) > 22:
                        cpu = cpu[:20] + "…"
                    specs.append(cpu)
                if spec_obj.get("ram"):
                    specs.append(spec_obj["ram"])
                if spec_obj.get("storage"):
                    specs.append(spec_obj["storage"])

                rating = p.get("rating")
                rating_str = f"⭐ {float(rating):.1f}" if rating else ""

                header = f"{i}. *{name}*"
                if price_str:
                    header += f" — {price_str}"
                lines.append(header)
                if specs:
                    lines.append(f"   {' · '.join(specs)}")
                if rating_str:
                    lines.append(f"   {rating_str}")

    # ── Quick reply suggestions ───────────────────────────────────────────────
    if response.quick_replies:
        lines.append("")
        lines.append("💬 *You can ask:*")
        for qr in response.quick_replies[:3]:
            lines.append(f"• {qr}")

    return "\n".join(lines).strip()


class ChatTextResponse(BaseModel):
    """Slim response for messaging-app clients (OpenClaw, WhatsApp bots, etc.)"""
    text: str
    session_id: str
    response_type: str


@app.post("/chat-text", response_model=ChatTextResponse)
async def chat_text(request: ChatRequest):
    """
    Text-only variant of /chat for use with OpenClaw and other messaging
    adapters.  Returns a pre-formatted plain-text string instead of the
    full JSON product graph so the skill doesn't have to do any formatting.

    Usage in OpenClaw skill:
        POST /chat-text  { message, session_id }
        → { text: "📦 Top Picks:\\n1. Dell XPS ...", session_id, response_type }
    """
    response = await process_chat(request)
    return ChatTextResponse(
        text=_format_response_as_text(response),
        session_id=response.session_id,
        response_type=response.response_type,
    )


# ─── OpenClaw skill file endpoint ────────────────────────────────────────────
# Serves the idss-shopping.js skill so OpenClaw can self-install it:
#   "Install this skill from URL: https://idss-backend-production.up.railway.app/skill"

@app.get("/skill")
async def get_skill():
    """Serve the IDSS Shopping OpenClaw skill as a downloadable JavaScript file."""
    from fastapi.responses import Response as _SkillResponse
    # Skill file lives at openclaw-skill/idss-shopping.js relative to /app in the container
    _skill_path = Path(__file__).parent.parent.parent / "openclaw-skill" / "idss-shopping.js"
    if not _skill_path.exists():
        raise HTTPException(status_code=404, detail="Skill file not found. Check container build.")
    return _SkillResponse(
        content=_skill_path.read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Content-Disposition": 'attachment; filename="idss-shopping.js"'},
    )


# ─── OpenClaw server-to-server webhook ───────────────────────────────────────
# OpenClaw calls POST /openclaw/message when a user sends a shopping query via
# WhatsApp, Telegram, Discord, etc.  We process it through the same AI pipeline
# as /chat-text and return a pre-formatted text string for OpenClaw to forward.
#
# Authentication: HMAC-SHA256 over the raw JSON body.
#   Header: X-OpenClaw-Signature: sha256=<hex>
#   If OPENCLAW_WEBHOOK_SECRET is not set, verification is skipped (dev mode).

import hmac as _hmac
import hashlib as _hashlib


class OpenClawWebhookRequest(BaseModel):
    """Payload that OpenClaw sends to our webhook."""
    message: str
    session_id: Optional[str] = None
    platform: Optional[str] = None   # "whatsapp" | "telegram" | "discord" | …
    user_id: Optional[str] = None


class OpenClawWebhookResponse(BaseModel):
    text: str
    session_id: str


def _verify_openclaw_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Verify the HMAC-SHA256 signature sent by OpenClaw.

    Returns True if:
      - OPENCLAW_WEBHOOK_SECRET is not configured (dev / open mode), OR
      - The supplied header matches sha256=HMAC(secret, raw_body)

    Returns False if the secret is set but the signature is missing or wrong.
    """
    secret = os.getenv("OPENCLAW_WEBHOOK_SECRET", "")
    if not secret:
        return True                           # dev mode — skip verification

    if not signature_header:
        return False                          # secret required but no header

    parts = signature_header.split("=", 1)
    if len(parts) != 2 or parts[0] != "sha256":
        return False                          # wrong algorithm prefix

    expected = _hmac.new(secret.encode(), raw_body, _hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, parts[1])


@app.post("/openclaw/message", response_model=OpenClawWebhookResponse)
async def openclaw_message(request: Request):
    """
    Server-to-server webhook for OpenClaw integrations.

    OpenClaw calls this endpoint when a user sends a shopping query via any
    connected messaging app (WhatsApp, iMessage, Telegram, Discord, Slack…).
    The message is processed through the IDSS AI pipeline and the response is
    returned as a plain-text string that OpenClaw forwards to the user.

    Auth: X-OpenClaw-Signature: sha256=<HMAC-SHA256 of raw body>
    Set OPENCLAW_WEBHOOK_SECRET on the server to enable signature validation.
    Leave unset for development / unauthenticated usage.

    Request body:
        {
          "message":    "find me a gaming laptop under $900",
          "session_id": "oc-<id>",          // optional – auto-generated if absent
          "platform":   "whatsapp",          // optional metadata
          "user_id":    "user123"            // optional metadata
        }

    Response:
        { "text": "📦 *Top Picks:* ...", "session_id": "oc-<id>" }
    """
    raw_body = await request.body()

    # ── Signature verification ────────────────────────────────────────────────
    signature = request.headers.get("X-OpenClaw-Signature")
    if not _verify_openclaw_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid OpenClaw signature")

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        payload = OpenClawWebhookRequest.model_validate_json(raw_body)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid request body")

    # ── Process via the IDSS AI pipeline ─────────────────────────────────────
    session_id = payload.session_id or f"oc-{_uuid.uuid4().hex[:8]}"
    chat_req = ChatRequest(message=payload.message, session_id=session_id)
    response = await process_chat(chat_req)

    return OpenClawWebhookResponse(
        text=_format_response_as_text(response),
        session_id=response.session_id,
    )


# ─── eBay deal search ─────────────────────────────────────────────────────────

class EbayResult(BaseModel):
    title: str
    price: Optional[str] = None
    condition: Optional[str] = None
    url: str
    shipping: Optional[str] = None


class EbaySearchResponse(BaseModel):
    query: str
    max_price: Optional[float] = None
    results: List[EbayResult]
    search_url: str
    source: str  # "api" | "rss" | "url_only"


@app.get("/search/ebay", response_model=EbaySearchResponse)
async def search_ebay(
    q: str,
    max_price: Optional[float] = None,
    condition: Optional[str] = None,   # "new" | "used" | "refurbished"
    sort: Optional[str] = "best-match",  # "price-low" | "best-match" | "ending-soon"
    limit: int = 5,
):
    """
    Search eBay listings and return structured results.

    Priority:
      1. eBay Finding API (if EBAY_APP_ID env var is set) — authoritative, fast
      2. eBay RSS feed fallback — no API key needed, parses XML
      3. URL-only — just returns the search URL so OpenClaw can browse it

    Used by the OpenClaw IDSS skill for cross-referencing AI picks against
    live eBay prices and making purchase decisions.
    """
    import xml.etree.ElementTree as _ET
    import httpx as _httpx
    from urllib.parse import quote as _quote

    # ── Build canonical eBay search URL ──────────────────────────────────────
    sort_codes = {"price-low": "15", "best-match": "12", "ending-soon": "1"}
    sort_code = sort_codes.get(sort or "best-match", "12")
    condition_codes = {"new": "LH_New=1", "used": "LH_Used=1", "refurbished": "LH_Refurb=1"}

    url_params = [f"_nkw={_quote(q)}", f"_sop={sort_code}", "_ipg=25"]
    if max_price:
        url_params.append(f"_udhi={int(max_price)}")
    if condition and condition in condition_codes:
        url_params.append(condition_codes[condition])

    search_url = "https://www.ebay.com/sch/i.html?" + "&".join(url_params)

    # ── Attempt 1: eBay Finding API ───────────────────────────────────────────
    ebay_app_id = os.getenv("EBAY_APP_ID", "")
    if ebay_app_id:
        try:
            api_params = {
                "OPERATION-NAME": "findItemsByKeywords",
                "SERVICE-VERSION": "1.0.0",
                "SECURITY-APPNAME": ebay_app_id,
                "RESPONSE-DATA-FORMAT": "JSON",
                "GLOBAL-ID": "EBAY-US",
                "keywords": q,
                "paginationInput.entriesPerPage": str(min(limit, 10)),
                "sortOrder": "BestMatch" if sort == "best-match" else "PricePlusShippingLowest",
            }
            if max_price:
                api_params["itemFilter(0).name"] = "MaxPrice"
                api_params["itemFilter(0).value"] = str(max_price)
                api_params["itemFilter(0).paramName"] = "Currency"
                api_params["itemFilter(0).paramValue"] = "USD"
            if condition == "new":
                api_params["itemFilter(1).name"] = "Condition"
                api_params["itemFilter(1).value"] = "1000"
            elif condition == "used":
                api_params["itemFilter(1).name"] = "Condition"
                api_params["itemFilter(1).value"] = "3000"

            async with _httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://svcs.ebay.com/services/search/FindingService/v1",
                    params=api_params,
                )
            data = resp.json()
            items = (
                data.get("findItemsByKeywordsResponse", [{}])[0]
                .get("searchResult", [{}])[0]
                .get("item", [])
            )
            results = []
            for item in items[:limit]:
                vcs = item.get("viewItemURL", [""])
                price_info = item.get("sellingStatus", [{}])[0].get("currentPrice", [{}])[0]
                price_val = price_info.get("__value__", "")
                cond = item.get("condition", [{}])[0].get("conditionDisplayName", [""])[0]
                ship = item.get("shippingInfo", [{}])[0].get("shippingType", [""])[0]
                results.append(EbayResult(
                    title=item.get("title", [""])[0],
                    price=f"${float(price_val):,.2f}" if price_val else None,
                    condition=cond or None,
                    url=vcs[0] if vcs else search_url,
                    shipping=ship or None,
                ))
            if results:
                return EbaySearchResponse(query=q, max_price=max_price, results=results,
                                          search_url=search_url, source="api")
        except Exception as _e:
            logger.warning("ebay_api_error: %s", _e)

    # ── Attempt 2: eBay RSS feed (no auth needed) ────────────────────────────
    try:
        rss_url = search_url + "&_rss=1"
        async with _httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; IDSS-Shopping/1.0)"
        }) as client:
            rss_resp = await client.get(rss_url)

        root = _ET.fromstring(rss_resp.text)
        ns = {"media": "http://search.yahoo.com/mrss/"}
        results = []

        for item in root.findall(".//item")[:limit]:
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")

            if title_el is None or link_el is None:
                continue

            title = (title_el.text or "").strip()
            link = (link_el.text or "").strip()

            # eBay RSS embeds price in the description HTML — extract it
            import re as _re_local
            desc_html = desc_el.text if desc_el is not None else ""
            price_match = _re_local.search(r"\$[\d,]+(?:\.\d{2})?", desc_html or "")
            price_str = price_match.group(0) if price_match else None

            results.append(EbayResult(title=title, price=price_str, url=link))

        if results:
            return EbaySearchResponse(query=q, max_price=max_price, results=results,
                                      search_url=search_url, source="rss")
    except Exception as _e:
        logger.warning("ebay_rss_error: %s", _e)

    # ── Attempt 3: URL-only fallback ─────────────────────────────────────────
    # OpenClaw's browser automation will open the URL and scrape the page itself.
    return EbaySearchResponse(
        query=q,
        max_price=max_price,
        results=[],
        search_url=search_url,
        source="url_only",
    )


# ─── Shareable chat link store ───────────────────────────────────────────────
# Persisted to PostgreSQL shared_chats table (created in lifespan startup).


class ShareRequest(BaseModel):
    messages: List[Dict[str, Any]]
    title: str = ""
    session_id: str = ""


class ShareResponse(BaseModel):
    share_id: str


@app.post("/share", response_model=ShareResponse)
async def create_share(request: ShareRequest, db: Session = Depends(get_db)):
    """Store a chat snapshot in the DB and return a short share_id."""
    share_id = _uuid.uuid4().hex[:8]
    db.execute(
        _sa_text(
            "INSERT INTO shared_chats (share_id, title, messages, session_id)"
            " VALUES (:sid, :title, CAST(:messages AS jsonb), :session_id)"
        ),
        {
            "sid": share_id,
            "title": request.title or "IDSS Chat",
            "messages": _json.dumps(request.messages),
            "session_id": request.session_id,
        },
    )
    db.commit()
    return ShareResponse(share_id=share_id)


@app.get("/share/{share_id}")
async def get_share(share_id: str, db: Session = Depends(get_db)):
    """Retrieve a stored chat snapshot by share_id."""
    row = db.execute(
        _sa_text(
            "SELECT title, messages, created_at FROM shared_chats WHERE share_id = :sid"
        ),
        {"sid": share_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Shared chat not found")
    created_iso = row.created_at.isoformat()
    if not created_iso.endswith("Z"):
        created_iso = created_iso.replace("+00:00", "") + "Z"
    return {"title": row.title, "messages": row.messages, "created_at": created_iso}


# ─── Product Q&A — dedicated single-product question answering ───────────────


class ProductQARequest(BaseModel):
    question: str
    product_context: Dict[str, Any]   # full product object from the frontend
    product_id: Optional[str] = None  # if provided, backend fetches full DB record to enrich context
    history: List[Dict[str, str]] = []  # prior panel turns [{"role":…,"content":…}]
    session_id: Optional[str] = None  # if provided, session preferences are added to context


class ProductQAResponse(BaseModel):
    answer: str


def _format_product_context(pc: Dict[str, Any]) -> str:
    """Flatten a product dict into a readable context string for the LLM."""
    name = pc.get("name") or pc.get("title") or "Product"
    brand = pc.get("brand") or ""
    price = pc.get("price")
    desc = pc.get("description") or pc.get("short_description") or ""

    # Collect specs from all possible layouts:
    #   pc["attributes"]         — top-level attributes blob
    #   pc["laptop"]["attributes"] — full JSONB attributes blob (richest source)
    #   pc["laptop"]["specs"]    — normalized spec fields
    attrs: Dict[str, Any] = pc.get("attributes") or {}
    lp = pc.get("laptop")
    laptop_attrs: Dict[str, Any] = (lp.get("attributes") or {}) if isinstance(lp, dict) else {}
    specs: Dict[str, Any] = (lp.get("specs") or {}) if isinstance(lp, dict) else {}
    # Merge order: laptop_attrs < top-level attrs < normalized specs (most specific wins)
    merged = {**laptop_attrs, **attrs, **{k: v for k, v in specs.items() if v is not None}}

    priority_keys = [
        "ram", "graphics", "processor", "storage", "display",
        "battery_life", "weight", "os", "screen_size", "refresh_rate", "resolution",
    ]
    lines = [f"Product: {name}"]
    if brand:
        lines.append(f"Brand: {brand}")
    if price is not None:
        price_str = f"${float(price):,.0f}" if isinstance(price, (int, float)) else str(price)
        lines.append(f"Price: {price_str}")

    lines.append("Specifications:")
    shown: set[str] = set()
    for k in priority_keys:
        v = merged.get(k)
        if v is not None:
            lines.append(f"  {k.replace('_', ' ').title()}: {v}")
            shown.add(k)
    for k, v in merged.items():
        if k not in shown and v is not None:
            lines.append(f"  {k.replace('_', ' ').title()}: {v}")

    if desc:
        lines.append(f"Description: {desc[:600]}")

    reviews = pc.get("reviews") or pc.get("review_summary") or ""
    if reviews:
        lines.append(f"Customer Reviews: {str(reviews)[:500]}")

    rating = pc.get("rating") or pc.get("average_rating")
    review_count = pc.get("review_count") or pc.get("num_reviews")
    if rating:
        line = f"Rating: {rating}/5"
        if review_count:
            line += f" ({review_count} reviews)"
        lines.append(line)

    return "\n".join(lines)


def _product_qa_fallback(question: str, context: dict) -> str:
    """Return a structured answer from product fields when the LLM is unavailable."""
    q = question.lower()
    name = context.get("name") or context.get("title") or "This product"

    # Price
    if any(w in q for w in ("price", "cost", "how much", "expensive", "cheap")):
        price = context.get("price_value") or context.get("price")
        if price:
            return f"{name} is priced at ${float(price):,.2f}."

    # RAM
    if "ram" in q or "memory" in q:
        attrs = context.get("attributes") or {}
        ram = attrs.get("ram") or attrs.get("memory") or context.get("ram")
        if ram:
            return f"{name} has {ram} of RAM."

    # Storage
    if "storage" in q or "ssd" in q or "hdd" in q or "hard drive" in q:
        attrs = context.get("attributes") or {}
        storage = attrs.get("storage") or attrs.get("hard_drive") or context.get("storage")
        if storage:
            return f"{name} has {storage} of storage."

    # Screen / display
    if any(w in q for w in ("screen", "display", "inch", "resolution")):
        attrs = context.get("attributes") or {}
        screen = attrs.get("screen_size") or attrs.get("display") or context.get("screen_size")
        if screen:
            return f"{name} has a {screen} display."

    # Rating
    if any(w in q for w in ("rating", "review", "rated", "score")):
        rating = context.get("rating")
        count = context.get("rating_count")
        if rating:
            cnt_str = f" across {count} reviews" if count else ""
            return f"{name} is rated {rating}/5{cnt_str}."

    # Warranty / return
    if "warranty" in q:
        w = context.get("warranty")
        if w:
            return f"Warranty: {w}"
    if "return" in q:
        r = context.get("return_policy")
        if r:
            return f"Return policy: {r}"

    # Brand
    if any(w in q for w in ("brand", "manufacturer", "made by", "who makes", "company")):
        brand = context.get("brand")
        if brand:
            return f"{name} is made by {brand}."

    # Generic fallback
    specs = []
    attrs = context.get("attributes") or {}
    for key in ("ram", "storage", "processor", "cpu", "screen_size", "battery"):
        val = attrs.get(key) or context.get(key)
        if val:
            specs.append(f"{key.replace('_', ' ').title()}: {val}")
    if specs:
        return f"Here's what I know about {name}: " + ", ".join(specs[:4]) + "."

    return (
        f"I don't have enough detail to answer that specific question about {name} right now. "
        "Try checking the product listing for full specifications."
    )


@app.post("/product-qa", response_model=ProductQAResponse)
async def product_qa(request: ProductQARequest):
    """Answer a question about a specific product using its data as LLM context.

    If product_id is supplied the backend fetches the full DB record and merges
    it with product_context so the LLM always has complete spec data.
    """
    context = dict(request.product_context)

    # Enrich with full DB record when product_id is available
    if request.product_id:
        try:
            from app.tools.supabase_product_store import get_product_store
            store = get_product_store()
            db_products = await asyncio.to_thread(
                store.get_by_ids, [request.product_id]
            )
            if db_products:
                db_rec = db_products[0] if isinstance(db_products[0], dict) else vars(db_products[0])
                # DB record enriches context; frontend values (rating, reviews) take precedence
                context = {**db_rec, **context}
        except Exception as _enrich_err:
            logger.warning("product_qa_enrich_failed %s", _enrich_err)

    product_text = _format_product_context(context)

    # Build user-preferences context from session when available
    preferences_text = ""
    if request.session_id:
        try:
            from agent.interview.session_manager import get_session_manager
            sm = get_session_manager()
            sess = sm.get_session(request.session_id)
            prefs = getattr(sess, "agent_filters", {}) or {}
            if prefs:
                pref_lines = []
                _pref_labels = {
                    "budget": "Budget", "brand": "Preferred brand", "use_case": "Use case",
                    "min_ram_gb": "Minimum RAM (GB)", "os": "OS",
                    "excluded_brands": "Excluded brands",
                    "good_for_gaming": "Good for gaming", "good_for_ml": "Good for ML/AI",
                }
                for k, v in prefs.items():
                    if v and not k.startswith("_"):
                        label = _pref_labels.get(k, k.replace("_", " ").title())
                        pref_lines.append(f"  {label}: {v}")
                if pref_lines:
                    preferences_text = "\n\nUSER PREFERENCES (gathered during this session):\n" + "\n".join(pref_lines)
        except Exception:
            pass  # Preferences are non-critical — never block product Q&A

    system_prompt = (
        "You are a knowledgeable product expert. The user is asking about the product below. "
        "Answer concisely (2–4 sentences) and accurately. "
        "Use the user's stated preferences to tailor your answer when relevant "
        "(e.g. if they care about ML/AI workloads, emphasise GPU and RAM specs). "
        "If the data doesn't contain a direct answer, give your best general-knowledge response "
        "and clearly note it. Never say 'Based on the data provided' — just answer naturally.\n\n"
        f"PRODUCT DATA:\n{product_text}"
        f"{preferences_text}"
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in request.history[-6:]:   # last 3 turns for context
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": request.question})

    try:
        from openai import OpenAI as _OpenAI
        client = _OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4o-mini",
            messages=messages,   # type: ignore[arg-type]
            max_tokens=350,
            temperature=0.3,
        )
        answer = resp.choices[0].message.content or "I couldn't generate a response."
    except Exception as exc:
        logger.error("product_qa_failed %s", exc)
        # Graceful fallback: extract answer directly from product context fields
        answer = _product_qa_fallback(request.question, context)

    return ProductQAResponse(answer=answer)


@app.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Get current session state."""
    return get_session_state(session_id)


@app.post("/session/reset", response_model=ResetResponse)
async def reset_session_endpoint(request: ResetRequest):
    """Reset session or create new one."""
    return reset_session(request.session_id)


@app.delete("/session/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Delete a session."""
    return delete_session(session_id)


@app.get("/sessions")
async def list_sessions_endpoint():
    """List all active sessions."""
    return list_sessions()


#
# Merchant Center Feed Export
# 

@app.get("/export/merchant-feed")
async def export_merchant_feed(
    format: str = "json",
    limit: int = None,
    category: str = None,
    db: Session = Depends(get_db)
):
    """
    Export products in Google Merchant Center compatible format.
    
    Supports:
    - JSON (default): Programmatic access with full metadata
    - XML: Google Shopping Content API format
    - CSV: Simple spreadsheet format
    
    Query Parameters:
    - format: Export format (json|xml|csv), default=json
    - limit: Maximum number of products to export
    - category: Filter by product category
    
    Returns:
    Product feed in specified format
    
    Reference: https://github.com/Universal-Commerce-Protocol/ucp
    """
    result = export_feed(db, format=format, limit=limit, category=category)
    
    if format == "xml":
        from fastapi.responses import Response
        return Response(content=result, media_type="application/xml")
    elif format == "csv":
        from fastapi.responses import Response
        return Response(content=result, media_type="text/csv")
    else:
        return result


# 
# Multi-LLM Tool Discovery Endpoints
# 

@app.get("/tools")
def list_tools():
    """
    List all available MCP tools in canonical format.
    
    This is the provider-neutral tool discovery endpoint.
    Use provider-specific endpoints (/tools/openai, /tools/gemini, /tools/claude)
    for LLM-specific formats.
    """
    return {
        "tools": ALL_TOOLS,
        "total_count": len(ALL_TOOLS),
        "providers_supported": ["openai", "gemini", "claude"]
    }


@app.get("/tools/openai")
def list_tools_openai():
    """
    List all tools in OpenAI function calling format.
    
    Returns tools formatted for OpenAI's function calling API.
    Use this to register MCP tools with GPT-4, GPT-3.5, etc.
    """
    return {
        "functions": get_all_tools_for_provider("openai"),
        "provider": "openai",
        "documentation": "https://platform.openai.com/docs/guides/function-calling"
    }


@app.get("/tools/gemini")
def list_tools_gemini():
    """
    List all tools in Google Gemini function declarations format.
    
    Returns tools formatted for Gemini's function calling API.
    Use this to register MCP tools with Gemini Pro, Gemini Ultra, etc.
    """
    return {
        "functions": get_all_tools_for_provider("gemini"),
        "provider": "gemini",
        "documentation": "https://ai.google.dev/gemini-api/docs/function-calling"
    }


@app.get("/tools/claude")
def list_tools_claude():
    """
    List all tools in Claude tool use format.
    
    Returns tools formatted for Claude's tool use API.
    Use this to register MCP tools with Claude 3 Opus, Sonnet, Haiku, etc.
    """
    return {
        "tools": get_all_tools_for_provider("claude"),
        "provider": "claude",
        "documentation": "https://docs.anthropic.com/claude/docs/tool-use"
    }


class ToolExecutionRequest(BaseModel):
    """Universal tool execution request."""
    tool_name: str
    parameters: Dict[str, Any]


@app.post("/tools/execute")
async def execute_tool(
    request: ToolExecutionRequest,
    db: Session = Depends(get_db)
):
    """
    Universal tool executor for multi-LLM support.
    
    Accepts tool calls from any LLM provider and routes to the appropriate MCP endpoint.
    This enables OpenAI, Gemini, and Claude to all call the same MCP tools.
    
    Args:
        tool_name: Name of the tool to execute (e.g., "search_products")
        parameters: Tool parameters as a dictionary
    
    Returns:
        Tool execution result in standard MCP format
    """
    tool_name = request.tool_name
    params = request.parameters
    
    try:
        # Route to appropriate endpoint based on tool name
        if tool_name == "search_products":
            search_req = SearchProductsRequest(**params)
            return await search_products(search_req, db)
        
        elif tool_name == "get_product":
            get_req = GetProductRequest(**params)
            return get_product(get_req, db)
        
        elif tool_name == "add_to_cart":
            cart_req = AddToCartRequest(**params)
            return add_to_cart(cart_req, db)
        
        elif tool_name == "checkout":
            checkout_req = CheckoutRequest(**params)
            return checkout(checkout_req, db)
        
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{tool_name}' not found. Available tools: search_products, get_product, add_to_cart, checkout"
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Tool execution error: {str(e)}"
        )


# 
# Tool-Call Endpoints (Main MCP API)
# 

@app.post("/api/search-products", response_model=SearchProductsResponse)
async def api_search_products(
    request: SearchProductsRequest,
    db: Session = Depends(get_db)
):
    """
    Search for products in the catalog.

    Tool-call endpoint for product discovery.
    Supports free-text search, structured filters, and pagination.

    Returns: List of product summaries with standard response envelope
    """
    return await search_products(request, db)


# ---------------------------------------------------------------------------
# Merchant-agent contract surface (ARCHITECTURE.md step 4)
# ---------------------------------------------------------------------------
# /merchant/search speaks StructuredQuery → list[Offer] per ARCHITECTURE.md.
# For now this is a thin wrapper over the existing search_products handler;
# the merchant team can refactor internals behind this endpoint freely
# without touching the shopping agent.

from typing import List as _List
from app.contract import StructuredQuery, Offer


@app.post("/merchant/search", response_model=_List[Offer])
async def merchant_search(
    query: StructuredQuery,
    db: Session = Depends(get_db),
) -> _List[Offer]:
    """
    Merchant-agent entry point. Accepts a StructuredQuery, returns ranked
    Offers. The shopping agent MUST NOT import anything from mcp-server/app/
    except `app.contract` — everything else is merchant-internal.
    """
    # Map the contract into the legacy SearchProductsRequest. For now both
    # hard_filters and soft_preferences are merged into the flat `filters`
    # dict because the internal retrieval stack doesn't yet distinguish
    # them; the split exists in the contract so merchant scoring can
    # evolve without agent changes.
    merged_filters: Dict[str, Any] = {**query.hard_filters, **query.soft_preferences}
    if "category" not in merged_filters and query.domain:
        merged_filters["category"] = query.domain

    # Agent hints via user_context:
    #  - "query": free-text for KG substring matching
    #  - "exclude_ids": products to omit (pagination / "show more" flows)
    _ctx = query.user_context if isinstance(query.user_context, dict) else {}
    text_query = _ctx.get("query")
    exclude_ids = list(_ctx.get("exclude_ids") or [])
    exclude_set = set(exclude_ids)

    # Over-fetch so that after server-side exclusion we can still return
    # top_k. Cap at the SearchProductsRequest upper bound (100).
    over_fetch = min(query.top_k + len(exclude_ids), 100)
    legacy_req = SearchProductsRequest(
        query=text_query,
        filters=merged_filters,
        limit=over_fetch,
    )
    resp = await search_products(legacy_req, db)

    merchant_id = os.environ.get("MERCHANT_ID", "default")
    raw = (resp.data.products if resp.data and resp.data.products else [])
    # Drop excluded IDs server-side for parity with the direct-Supabase path.
    products = [p for p in raw if p.product_id not in exclude_set][: query.top_k]
    n = max(len(products), 1)
    offers: _List[Offer] = []
    for i, p in enumerate(products):
        offers.append(Offer(
            merchant_id=merchant_id,
            product_id=p.product_id,
            # Placeholder score: monotone decreasing by server-side rank.
            # Replace with a real per-signal score when the retrieval stack
            # exposes one (tracked in #34).
            score=round(1.0 - (i / n), 4),
            score_breakdown={},
            product=p,
            rationale=p.reason or "",
        ))
    return offers


@app.post("/api/get-product", response_model=GetProductResponse, response_model_exclude_none=True)
def api_get_product(
    request: GetProductRequest,
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a single product.
    
    Tool-call endpoint for product details.
    Uses cache-aside pattern for performance.
    
    IDs-only: Accepts product_id, never product name.
    
    Returns: Full product details or NOT_FOUND
    """
    return get_product(request, db)


@app.post("/api/add-to-cart", response_model=AddToCartResponse)
def api_add_to_cart(
    request: AddToCartRequest,
    db: Session = Depends(get_db)
):
    """
    Add a product to a shopping cart.
    
    Execution endpoint with strict validation.
    IDs-only: Accepts product_id, never product name.
    
    Validates:
    - Product exists
    - Sufficient inventory
    - Creates cart if it doesn't exist
    
    Returns: Updated cart or OUT_OF_STOCK/NOT_FOUND constraint
    """
    return add_to_cart(request, db)


@app.post("/api/checkout", response_model=CheckoutResponse)
def api_checkout(
    request: CheckoutRequest,
    db: Session = Depends(get_db)
):
    """
    Complete checkout for a cart.
    
    Execution endpoint that finalizes a purchase.
    IDs-only: Accepts cart_id, payment_method_id, address_id.
    
    Validates:
    - Cart exists and has items
    - All items still in stock
    - Decrements inventory on success
    
    Returns: Order confirmation or constraints
    """
    return checkout(request, db)


# 
# IDSS-Specific Endpoints (Vehicle Recommendations)
# 

@app.post("/api/idss/search-products", response_model=SearchProductsResponse)
async def api_search_products_idss(request: SearchProductsRequest):
    """
    Search for vehicles using MCP vehicle_search (Supabase).
    
    Returns: List of vehicles formatted as products
    """
    return await search_products_idss(request)


# 
# Real Estate Endpoints
# 

@app.post("/api/real-estate/search-products", response_model=SearchProductsResponse)
async def api_search_real_estate(request: SearchProductsRequest):
    """
    Search for properties using Real Estate backend.
    
    This endpoint bridges the MCP product format to property listings.
    
    Returns: List of properties formatted as products
    """
    return await search_products_universal(request, product_type="real_estate")


@app.post("/api/real-estate/get-product", response_model=GetProductResponse)
async def api_get_property(request: GetProductRequest):
    """
    Get detailed information about a single property.
    
    IDs-only: Accepts property_id as product_id (format: PROP-###).
    
    Returns: Full property details formatted as product
    """
    return await get_product_universal(request)


# 
# Travel Endpoints
# 

@app.post("/api/travel/search-products", response_model=SearchProductsResponse)
async def api_search_travel(request: SearchProductsRequest):
    """
    Search for travel bookings (flights, hotels, packages) using Travel backend.
    
    This endpoint bridges the MCP product format to travel listings.
    
    Returns: List of travel options formatted as products
    """
    return await search_products_universal(request, product_type="travel")


@app.post("/api/travel/get-product", response_model=GetProductResponse)
async def api_get_travel(request: GetProductRequest):
    """
    Get detailed information about a single travel booking.
    
    IDs-only: Accepts booking_id as product_id (format: BOOK-###).
    
    Returns: Full booking details formatted as product
    """
    return await get_product_universal(request)


# 
# UCP (Universal Commerce Protocol) Endpoints
# 

@app.post("/ucp/search", response_model=UCPSearchResponse)
async def ucp_search_endpoint(
    request: UCPSearchRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible product search endpoint.
    
    Implements Google's Universal Commerce Protocol for agentic commerce.
    Maps UCP format to MCP search_products tool.
    
    Reference: https://github.com/Universal-Commerce-Protocol/ucp
    """
    response = await ucp_search(request, db, base_url="http://localhost:8001")
    # Log event for research replay
    log_ucp_event(db, "ucp_search", "/ucp/search", request, response)
    return response


@app.post("/ucp/get-product", response_model=UCPGetProductResponse)
@app.post("/ucp/get_product", response_model=UCPGetProductResponse)
async def ucp_get_product_endpoint(
    request: UCPGetProductRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible product detail endpoint.
    
    Implements Google's Universal Commerce Protocol for product retrieval.
    Maps UCP format to MCP get_product tool.
    """
    response = await ucp_get_product(request, db, base_url="http://localhost:8001")
    # Log event for research replay
    log_ucp_event(db, "ucp_get_product", "/ucp/get_product", request, response)
    return response


@app.post("/ucp/add_to_cart", response_model=UCPAddToCartResponse)
async def ucp_add_to_cart_endpoint(
    request: UCPAddToCartRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible add to cart endpoint.
    
    Implements Google's Universal Commerce Protocol for cart management.
    Maps UCP format to MCP add_to_cart tool.
    """
    logger.info("mcp_ucp_request: path=/ucp/add_to_cart payload=%s", request.model_dump())
    response = await ucp_add_to_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/add_to_cart status=%s", response.status)
    # Log event for research replay
    log_ucp_event(db, "ucp_add_to_cart", "/ucp/add_to_cart", request, response, session_id=response.cart_id)
    return response


@app.post("/ucp/checkout", response_model=UCPCheckoutResponse)
async def ucp_checkout_endpoint(
    request: UCPCheckoutRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible checkout endpoint.
    
    Implements Google's Universal Commerce Protocol for order placement.
    Maps UCP format to MCP checkout tool.
    
    Note: Minimal happy-path implementation for research purposes.
    Production would require payment processing, fraud detection, etc.
    """
    logger.info("mcp_ucp_request: path=/ucp/checkout payload=%s", request.model_dump())
    response = await ucp_checkout(request, db)
    logger.info("mcp_ucp_response: path=/ucp/checkout status=%s order_id=%s", response.status, getattr(response, "order_id", None))
    # Log event for research replay
    log_ucp_event(db, "ucp_checkout", "/ucp/checkout", request, response, session_id=request.parameters.cart_id)
    return response


@app.post("/ucp/get_cart", response_model=UCPGetCartResponse)
async def ucp_get_cart_endpoint(request: UCPGetCartRequest, db: Session = Depends(get_db)):
    """UCP get_cart. Agent sends this to fetch cart (e.g. cart_id = user_id)."""
    logger.info("mcp_ucp_request: path=/ucp/get_cart payload=%s", request.model_dump())
    response = await ucp_get_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/get_cart status=%s item_count=%s", response.status, response.item_count)
    log_ucp_event(db, "ucp_get_cart", "/ucp/get_cart", request, response, session_id=request.parameters.cart_id)
    return response


@app.post("/ucp/remove_from_cart", response_model=UCPRemoveFromCartResponse)
async def ucp_remove_from_cart_endpoint(request: UCPRemoveFromCartRequest, db: Session = Depends(get_db)):
    """UCP remove_from_cart."""
    logger.info("mcp_ucp_request: path=/ucp/remove_from_cart payload=%s", request.model_dump())
    response = await ucp_remove_from_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/remove_from_cart status=%s", response.status)
    log_ucp_event(db, "ucp_remove_from_cart", "/ucp/remove_from_cart", request, response, session_id=request.parameters.cart_id)
    return response


@app.post("/ucp/update_cart", response_model=UCPUpdateCartResponse)
async def ucp_update_cart_endpoint(request: UCPUpdateCartRequest, db: Session = Depends(get_db)):
    """UCP update_cart (set quantity; 0 = remove)."""
    logger.info("mcp_ucp_request: path=/ucp/update_cart payload=%s", request.model_dump())
    response = await ucp_update_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/update_cart status=%s", response.status)
    log_ucp_event(db, "ucp_update_cart", "/ucp/update_cart", request, response, session_id=request.parameters.get_cart_id())
    return response


# 
# UCP Native Checkout Endpoints (per Google UCP Guide)
# 

from app.ucp_checkout import (
    CreateCheckoutSessionRequest, UpdateCheckoutSessionRequest, CompleteCheckoutRequest,
    create_checkout_session, get_checkout_session, update_checkout_session,
    complete_checkout_session, cancel_checkout_session, UCPCheckoutSession
)


@app.post("/ucp/checkout-sessions", response_model=UCPCheckoutSession)
async def ucp_create_checkout_session(
    request: CreateCheckoutSessionRequest,
    db: Session = Depends(get_db)
):
    """
    Create a new UCP checkout session.
    
    Per Google UCP Guide: POST /checkout-sessions
    Trigger: User clicks "Buy" on a product.
    """
    response = create_checkout_session(request, db)
    # Log event for research replay (non-critical, don't let it crash checkout)
    try:
        log_ucp_event(db, "ucp_create_checkout_session", "/ucp/checkout-sessions", request, response, session_id=response.id)
    except Exception:
        pass
    return response


@app.get("/ucp/checkout-sessions/{session_id}", response_model=UCPCheckoutSession)
async def ucp_get_checkout_session(session_id: str, db: Session = Depends(get_db)):
    """
    Get checkout session by ID.
    
    Per Google UCP Guide: GET /checkout-sessions/{id}
    """
    session = get_checkout_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_get_checkout_session", f"/ucp/checkout-sessions/{session_id}", {"session_id": session_id}, session, session_id=session_id)
    return session


@app.put("/ucp/checkout-sessions/{session_id}", response_model=UCPCheckoutSession)
async def ucp_update_checkout_session(
    session_id: str,
    request: UpdateCheckoutSessionRequest,
    db: Session = Depends(get_db)
):
    """
    Update checkout session.
    
    Per Google UCP Guide: PUT /checkout-sessions/{id}
    Recalculates taxes and shipping when address changes.
    """
    session = update_checkout_session(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_update_checkout_session", f"/ucp/checkout-sessions/{session_id}", request, session, session_id=session_id)
    return session


@app.post("/ucp/checkout-sessions/{session_id}/complete", response_model=UCPCheckoutSession)
async def ucp_complete_checkout_session(
    session_id: str,
    request: CompleteCheckoutRequest,
    db: Session = Depends(get_db)
):
    """
    Complete checkout session and place order.
    
    Per Google UCP Guide: POST /checkout-sessions/{id}/complete
    Trigger: User clicks "Place Order".
    """
    session = complete_checkout_session(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_complete_checkout_session", f"/ucp/checkout-sessions/{session_id}/complete", request, session, session_id=session_id)
    return session


@app.post("/ucp/checkout-sessions/{session_id}/cancel", response_model=UCPCheckoutSession)
async def ucp_cancel_checkout_session(session_id: str, db: Session = Depends(get_db)):
    """
    Cancel checkout session.
    
    Per Google UCP Guide: POST /checkout-sessions/{id}/cancel
    """
    session = cancel_checkout_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_cancel_checkout_session", f"/ucp/checkout-sessions/{session_id}/cancel", {"session_id": session_id}, session, session_id=session_id)
    return session


# ─────────────────────────────────────────────────────────────────────────────
# ACP (OpenAI/Stripe Agentic Commerce Protocol) Endpoints
# Mirrors the UCP checkout session block above.
# External ACP agent flow: ChatGPT → POST /acp/checkout-sessions → merchant
# Reference: agenticcommerce.dev
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/acp/feed.json", tags=["ACP"])
async def acp_feed_json(db: Session = Depends(get_db)):
    """
    ACP Product Feed — JSON format.

    Returns the full product catalog in ACP feed format so OpenAI agents
    can discover and purchase products without a search step.
    """
    items = generate_product_feed(db)
    return {"protocol": "acp", "items": [i.model_dump() for i in items], "count": len(items)}


@app.get("/acp/feed.csv", tags=["ACP"])
async def acp_feed_csv(db: Session = Depends(get_db)):
    """
    ACP Product Feed — CSV format.

    Same data as /acp/feed.json but as text/csv for tools that prefer tabular feeds.
    """
    from fastapi.responses import PlainTextResponse
    items = generate_product_feed(db)
    if not items:
        return PlainTextResponse("id,title,price_dollars,availability,brand,category\n", media_type="text/csv")
    header = "id,title,price_dollars,currency,availability,inventory,brand,category,rating,product_url"
    rows = [header]
    for it in items:
        def _q(v: object) -> str:
            s = str(v) if v is not None else ""
            return f'"{s.replace(chr(34), chr(34)+chr(34))}"'
        rows.append(",".join([
            _q(it.id), _q(it.title), str(it.price_dollars), it.currency,
            it.availability, str(it.inventory),
            _q(it.brand or ""), _q(it.category or ""),
            str(it.rating or ""), _q(it.product_url),
        ]))
    return PlainTextResponse("\n".join(rows), media_type="text/csv")


@app.post("/acp/checkout-sessions", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_create_session_endpoint(
    request: ACPCreateSessionRequest,
    db: Session = Depends(get_db),
):
    """
    ACP — Create checkout session (status: pending).

    Per ACP spec: POST /checkout-sessions
    Trigger: agent has selected products and is ready to purchase.
    """
    session = await _acp_create(request, db)
    try:
        log_ucp_event(db, "acp_create_session", "/acp/checkout-sessions", request, session)
    except Exception:
        pass
    return session


@app.get("/acp/checkout-sessions/{session_id}", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_get_session_endpoint(session_id: str):
    """ACP — Get checkout session by ID."""
    session = await _acp_get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    return session


@app.post("/acp/checkout-sessions/{session_id}", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_update_session_endpoint(
    session_id: str,
    request: ACPUpdateSessionRequest,
    db: Session = Depends(get_db),
):
    """
    ACP — Update checkout session (buyer info, shipping address, shipping method).

    Per ACP spec 2026-01-30: POST /checkout-sessions/{id} (not PUT).
    Recalculates shipping + tax on every update.
    Transitions status to ready_for_payment.
    """
    session = await _acp_update(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    return session


@app.post("/acp/checkout-sessions/{session_id}/complete", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_complete_session_endpoint(
    session_id: str,
    request: ACPCompleteSessionRequest,
    db: Session = Depends(get_db),
):
    """
    ACP — Complete checkout session (place order).

    In production: Stripe delegated payment token is charged here.
    Returns completed session with order_id on success.
    """
    session = await _acp_complete(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    try:
        log_ucp_event(db, "acp_complete_session", f"/acp/checkout-sessions/{session_id}/complete", request, session)
    except Exception:
        pass
    return session


@app.post("/acp/checkout-sessions/{session_id}/cancel", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_cancel_session_endpoint(session_id: str):
    """ACP — Cancel checkout session."""
    session = await _acp_cancel(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    return session


@app.post("/acp/webhooks/orders", tags=["ACP"])
async def acp_order_webhook(payload: Dict[str, Any], request: Request):
    """
    ACP — Order event webhook from OpenAI/Stripe.

    Returns 200 immediately to avoid retries. Events are logged for async processing.
    Signature validation (X-ACP-Signature header) should be added before production use.
    """
    logger.info("acp_webhook_received: %s", payload)
    return {"received": True}


#
# Agent Action Endpoints (Frontend → API → Agent → UCP/ACP → MCP → Supabase)
# Frontend calls these with user_id (signed-in). When Supabase is configured,
# they use the Supabase cart table and products.inventory; otherwise in-memory fallback.
#

# In-memory user cart store: user_id → {product_id → {"qty": int, "product_snapshot": dict}}
# All cart ops go through Agent → UCP → MCP/Supabase (see action_* endpoints below).


class FetchCartRequest(BaseModel):
    user_id: str


class CartItemOut(BaseModel):
    id: str
    product_id: str
    product_snapshot: Dict[str, Any]
    quantity: int


class FetchCartResponse(BaseModel):
    status: str
    cart_id: Optional[str] = None
    items: Optional[List[CartItemOut]] = None
    item_count: Optional[int] = None
    error: Optional[str] = None


class AddToCartActionRequest(BaseModel):
    user_id: str
    product_id: str
    quantity: int = 1
    product_snapshot: Dict[str, Any] = {}


class RemoveFromCartRequest(BaseModel):
    user_id: str
    product_id: str


class CheckoutActionRequest(BaseModel):
    user_id: str
    items: Optional[List[Dict[str, Any]]] = None
    product_type: Optional[str] = None
    shipping_method: Optional[str] = "standard"


@app.post("/api/action/fetch-cart")
async def action_fetch_cart(request: FetchCartRequest, db: Session = Depends(get_db)):
    """Return the current cart for a user. Agent sends UCP get_cart to MCP over HTTP."""
    logger.info("action_api_request: action=%s user_id=%s", "fetch_cart", request.user_id)
    ucp_req = build_ucp_get_cart(request.user_id)
    logger.info("ucp_request: ucp_action=%s payload=%s", "get_cart", ucp_req.model_dump())
    response = await ucp_get_cart(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s item_count=%s", "get_cart", response.status, response.item_count)
    log_ucp_event(db, "ucp_get_cart", "/api/action/fetch-cart", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return FetchCartResponse(status="error", error=response.error or "Failed to get cart")
    items = [
        CartItemOut(id=it.id, product_id=it.product_id, product_snapshot=it.product_snapshot, quantity=it.quantity)
        for it in response.items
    ]
    return FetchCartResponse(status="success", cart_id=response.cart_id, items=items, item_count=response.item_count or len(items))


@app.post("/api/action/add-to-cart")
async def action_add_to_cart(request: AddToCartActionRequest, db: Session = Depends(get_db)):
    """Add (or increment) a product in the user's cart. Uses ACP or UCP based on COMMERCE_PROTOCOL."""
    logger.info("action_api_request: action=%s user_id=%s product_id=%s quantity=%s protocol=%s",
                "add_to_cart", request.user_id, request.product_id, request.quantity,
                "acp" if is_acp() else "ucp")

    if is_acp():
        # ACP path: create a checkout session with this product
        snapshot = request.product_snapshot or {}
        acp_req = build_acp_create_session(
            product_id=request.product_id,
            title=snapshot.get("name") or snapshot.get("title") or "Product",
            price_dollars=float(snapshot.get("price", 0)),
            quantity=request.quantity,
            image_url=snapshot.get("image_url") or snapshot.get("imageurl"),
        )
        session = await _acp_create(acp_req, db)
        if session.error:
            return {"status": "error", "error": session.error}
        return {"status": "success", "acp_session_id": session.id}

    # UCP path (default)
    ucp_req = build_ucp_add_to_cart(request.user_id, request.product_id, request.quantity, request.product_snapshot)
    logger.info("ucp_request: ucp_action=%s payload=%s", "add_to_cart", ucp_req.model_dump())
    response = await ucp_add_to_cart(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s", "add_to_cart", response.status)
    log_ucp_event(db, "ucp_add_to_cart", "/api/action/add-to-cart", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return {"status": "error", "error": response.error or "Add to cart failed"}
    return {"status": "success"}


@app.post("/api/action/remove-from-cart")
async def action_remove_from_cart(request: RemoveFromCartRequest, db: Session = Depends(get_db)):
    """Remove a product from the user's cart. Agent sends UCP remove_from_cart to MCP over HTTP."""
    logger.info("action_api_request: action=%s user_id=%s product_id=%s", "remove_from_cart", request.user_id, request.product_id)
    ucp_req = build_ucp_remove_from_cart(request.user_id, request.product_id)
    logger.info("ucp_request: ucp_action=%s payload=%s", "remove_from_cart", ucp_req.model_dump())
    response = await ucp_remove_from_cart(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s", "remove_from_cart", response.status)
    log_ucp_event(db, "ucp_remove_from_cart", "/api/action/remove-from-cart", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return {"status": "error", "error": response.error or "Remove failed"}
    return {"status": "success"}


@app.post("/api/action/checkout")
async def action_checkout(request: CheckoutActionRequest, db: Session = Depends(get_db)):
    """Checkout the user's cart. Uses ACP or UCP based on COMMERCE_PROTOCOL."""
    logger.info("action_api_request: action=%s user_id=%s shipping_method=%s protocol=%s",
                "checkout", request.user_id, request.shipping_method, "acp" if is_acp() else "ucp")

    if is_acp():
        # ACP path: complete the most-recent ACP session for this user.
        # In a real deployment, the session_id would be stored per-user.
        # Here we look for a pending session and complete it.
        from app.acp_endpoints import _acp_sessions
        pending = [s for s in _acp_sessions.values() if s.status in ("incomplete", "ready_for_payment")]
        if not pending:
            return {"status": "error", "error": "No active ACP checkout session"}
        # Complete the most-recently created session
        session_id = pending[-1].id
        acp_complete_req = build_acp_complete_session(payment_method="card")
        session = await _acp_complete(session_id, acp_complete_req, db)
        if not session or session.status != "completed":
            return {"status": "error", "error": (session.error if session else "Checkout failed")}
        return {"status": "success", "order_id": session.order_id, "acp_session_id": session.id}

    # UCP path (default)
    ucp_req = build_ucp_checkout(request.user_id, shipping_method=request.shipping_method or "standard")
    logger.info("ucp_request: ucp_action=%s payload=%s", "checkout", ucp_req.model_dump())
    response = await ucp_checkout(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s order_id=%s", "checkout", response.status, getattr(response, "order_id", None))
    log_ucp_event(db, "ucp_checkout", "/api/action/checkout", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return {"status": "error", "error": response.error or "Checkout failed", "details": getattr(response, "details", None)}
    return {"status": "success", "order_id": response.order_id}


# ─── Shipping / Tax calculation ──────────────────────────────────────────────

class ShippingCalcItem(BaseModel):
    product_id: str
    unit_price_cents: Optional[int] = None
    unit_price_dollars: Optional[float] = None
    quantity: int = 1
    weight_lbs: Optional[float] = None

class ShippingCalcRequest(BaseModel):
    state_code: str                          # 2-letter US state code
    shipping_method: str = "standard"        # standard | express | overnight
    items: List[ShippingCalcItem]

@app.post("/api/calculate-shipping")
async def api_calculate_shipping(request: ShippingCalcRequest):
    """
    Calculate shipping cost + state sales tax for a list of items.

    Returns subtotal, shipping, tax, and total — all in cents.
    Accepts all 50 US states + DC. Tax rates are combined state+avg-local (2025).
    """
    try:
        items_data = [item.model_dump(exclude_none=False) for item in request.items]
        result = calculate_shipping(
            state_code=request.state_code,
            shipping_method=request.shipping_method,
            items=items_data,
        )
        return {
            "status": "ok",
            "state_code": result.state_code,
            "state_name": result.state_name,
            "tax_rate_pct": result.tax_rate_pct,
            "shipping_method": result.shipping_method,
            "subtotal_cents": result.subtotal_cents,
            "shipping_cents": result.shipping_cents,
            "tax_cents": result.tax_cents,
            "total_cents": result.total_cents,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/states")
async def api_get_states():
    """Return all US states + DC with their tax rates, for populating UI dropdowns."""
    return {"status": "ok", "states": ALL_STATES}


# ─── Coupon / Discount code validation ───────────────────────────────────────

class CouponValidateRequest(BaseModel):
    code: str
    subtotal_cents: int
    shipping_cents: int = 0

@app.post("/api/validate-coupon")
async def api_validate_coupon(request: CouponValidateRequest):
    """
    Validate a coupon code and return the discount amount.

    Demo codes: STANFORD10, SAVE20, FREESHIP, WELCOME5, TECH50
    """
    result = validate_coupon(
        code=request.code,
        subtotal_cents=request.subtotal_cents,
        shipping_cents=request.shipping_cents,
    )
    return {
        "status": "ok",
        "valid": result.valid,
        "code": result.code,
        "description": result.description,
        "discount_type": result.discount_type,
        "discount_value": result.discount_value,
        "discount_cents": result.discount_cents,
        "error": result.error,
    }


#
# Development Server
#

if __name__ == "__main__":
    # Run server with auto-reload for development
    # Note: MCP server runs on port 8001 to avoid conflict with IDSS backend (port 8000)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
