#!/usr/bin/env python3
"""
Generate representative sample protocol trace files for Hannah's evaluation.

Shows the complete request/response shapes for MCP, UCP, and ACP protocols.
Output files:
  logs/mcp_traces.jsonl     — MCP (Anthropic) tool-call protocol
  logs/ucp_traces.jsonl     — UCP (Google) commerce adapter protocol
  logs/acp_traces.jsonl     — ACP (OpenAI/Stripe) checkout-session protocol

Run:  python scripts/generate_protocol_sample_logs.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)


# ─── Sample data ─────────────────────────────────────────────────────────────

SAMPLE_PRODUCT = {
    "product_id": "3f4e5a6b-7c8d-9e0f-a1b2-c3d4e5f60000",
    "name": "Dell XPS 15 Laptop",
    "brand": "Dell",
    "price": 1299.99,
    "inventory": 42,
    "category": "Electronics",
    "specs": {
        "ram_gb": 16,
        "storage_gb": 512,
        "display_inches": 15.6,
        "processor": "Intel Core i7-13700H",
    },
    "shipping_days": 3,
    "return_policy": "30-day returns",
    "warranty": "1-year limited warranty",
}

SAMPLE_PRODUCT_2 = {
    "product_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
    "name": "Apple MacBook Pro 14\"",
    "brand": "Apple",
    "price": 1999.99,
    "inventory": 18,
    "category": "Electronics",
    "specs": {
        "ram_gb": 18,
        "storage_gb": 512,
        "display_inches": 14.2,
        "processor": "Apple M3 Pro",
    },
    "shipping_days": 2,
    "return_policy": "30-day returns",
    "warranty": "1-year AppleCare",
}

CART_ID = "cart-user123-abc"
SESSION_ID = "session-alice-20260226"
ORDER_ID = "order-20260226-001"
ACP_SESSION_ID = "acp-session-9f8e7d6c-5b4a"


# ─── MCP traces ──────────────────────────────────────────────────────────────

MCP_TRACES = [
    # 1. search_products
    {
        "ts": "2026-02-26T10:00:01Z",
        "protocol": "mcp",
        "endpoint": "/api/search-products",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 172.4,
        "request_body": {
            "query": "lightweight laptop for college",
            "filters": {"max_price": 1500, "min_ram_gb": 8},
            "limit": 5,
            "session_id": SESSION_ID,
        },
        "response_body": {
            "status": "ok",
            "data": {
                "products": [SAMPLE_PRODUCT, SAMPLE_PRODUCT_2],
                "total_count": 2,
                "query_used": "lightweight laptop for college",
                "relaxation_applied": False,
            },
            "trace": {
                "request_id": "req-001",
                "cache_hit": False,
                "timings_ms": {"total": 172.4, "supabase": 137.1, "rerank": 22.5},
                "sources": ["supabase"],
            },
        },
    },
    # 2. get_product
    {
        "ts": "2026-02-26T10:00:04Z",
        "protocol": "mcp",
        "endpoint": "/api/get-product",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 105.2,
        "request_body": {
            "product_id": SAMPLE_PRODUCT["product_id"],
            "session_id": SESSION_ID,
        },
        "response_body": {
            "status": "ok",
            "data": {
                "product": SAMPLE_PRODUCT,
                "inventory_status": "in_stock",
            },
            "trace": {
                "request_id": "req-002",
                "cache_hit": True,
                "timings_ms": {"total": 105.2, "redis": 6.1},
                "sources": ["redis"],
            },
        },
    },
    # 3. add_to_cart
    {
        "ts": "2026-02-26T10:00:09Z",
        "protocol": "mcp",
        "endpoint": "/api/add-to-cart",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 178.3,
        "request_body": {
            "cart_id": CART_ID,
            "product_id": SAMPLE_PRODUCT["product_id"],
            "quantity": 1,
            "session_id": SESSION_ID,
        },
        "response_body": {
            "status": "ok",
            "data": {
                "cart_id": CART_ID,
                "items": [
                    {"product_id": SAMPLE_PRODUCT["product_id"], "quantity": 1,
                     "unit_price": 1299.99}
                ],
                "item_count": 1,
            },
            "trace": {"request_id": "req-003", "cache_hit": False,
                      "timings_ms": {"total": 178.3}, "sources": ["supabase"]},
        },
    },
    # 4. checkout
    {
        "ts": "2026-02-26T10:00:15Z",
        "protocol": "mcp",
        "endpoint": "/api/checkout",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 210.7,
        "request_body": {
            "cart_id": CART_ID,
            "session_id": SESSION_ID,
            "shipping_method": "standard",
        },
        "response_body": {
            "status": "ok",
            "data": {
                "order_id": ORDER_ID,
                "cart_id": CART_ID,
                "status": "confirmed",
                "items": [
                    {"product_id": SAMPLE_PRODUCT["product_id"], "quantity": 1,
                     "unit_price": 1299.99}
                ],
                "totals": {
                    "subtotal_cents": 129999,
                    "shipping_cents": 0,
                    "tax_cents": 11375,
                    "total_cents": 141374,
                },
            },
            "trace": {"request_id": "req-004", "cache_hit": False,
                      "timings_ms": {"total": 210.7}, "sources": ["supabase"]},
        },
    },
    # 5. tools/execute (agent calls MCP tool via JSON-RPC-style envelope)
    {
        "ts": "2026-02-26T10:00:22Z",
        "protocol": "mcp",
        "endpoint": "/tools/execute",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 189.6,
        "request_body": {
            "tool": "search_products",
            "parameters": {
                "query": "gaming laptop under $1500",
                "filters": {"max_price": 1500, "category": "Electronics"},
                "limit": 3,
            },
        },
        "response_body": {
            "tool": "search_products",
            "result": {
                "status": "ok",
                "data": {"products": [SAMPLE_PRODUCT], "total_count": 1},
            },
        },
    },
]


# ─── UCP traces ──────────────────────────────────────────────────────────────

UCP_TRACES = [
    # 1. ucp/search — Google's agent searching the merchant catalog
    {
        "ts": "2026-02-26T10:01:00Z",
        "protocol": "ucp",
        "endpoint": "/ucp/search",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 183.1,
        "request_body": {
            "query": "laptop computer",
            "filters": {"max_price": 1500},
            "page": 1,
            "page_size": 5,
        },
        "response_body": {
            "status": "success",
            "products": [
                {
                    "id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "brand": SAMPLE_PRODUCT["brand"],
                    "price_dollars": SAMPLE_PRODUCT["price"],
                    "currency": "USD",
                    "availability": "in_stock",
                    "inventory_count": SAMPLE_PRODUCT["inventory"],
                    "image_url": "https://example.com/dell-xps15.jpg",
                    "product_url": "/products/" + SAMPLE_PRODUCT["product_id"],
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 5,
        },
    },
    # 2. ucp/get_product
    {
        "ts": "2026-02-26T10:01:05Z",
        "protocol": "ucp",
        "endpoint": "/ucp/get_product",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 98.4,
        "request_body": {"product_id": SAMPLE_PRODUCT["product_id"]},
        "response_body": {
            "status": "success",
            "product": {
                "id": SAMPLE_PRODUCT["product_id"],
                "title": SAMPLE_PRODUCT["name"],
                "brand": SAMPLE_PRODUCT["brand"],
                "price_dollars": SAMPLE_PRODUCT["price"],
                "currency": "USD",
                "availability": "in_stock",
                "inventory_count": SAMPLE_PRODUCT["inventory"],
                "specs": SAMPLE_PRODUCT["specs"],
                "shipping_days": SAMPLE_PRODUCT["shipping_days"],
                "return_policy": SAMPLE_PRODUCT["return_policy"],
                "warranty": SAMPLE_PRODUCT["warranty"],
            },
        },
    },
    # 3. ucp/add_to_cart
    {
        "ts": "2026-02-26T10:01:09Z",
        "protocol": "ucp",
        "endpoint": "/ucp/add_to_cart",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 136.7,
        "request_body": {
            "cart_id": CART_ID,
            "product_id": SAMPLE_PRODUCT["product_id"],
            "quantity": 1,
        },
        "response_body": {
            "status": "success",
            "cart_id": CART_ID,
            "items": [
                {
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "quantity": 1,
                    "unit_price_dollars": SAMPLE_PRODUCT["price"],
                }
            ],
        },
    },
    # 4. ucp/checkout
    {
        "ts": "2026-02-26T10:01:14Z",
        "protocol": "ucp",
        "endpoint": "/ucp/checkout",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 220.0,
        "request_body": {
            "cart_id": CART_ID,
            "shipping_method": "standard",
        },
        "response_body": {
            "status": "success",
            "order_id": ORDER_ID,
            "cart_id": CART_ID,
            "order_status": "confirmed",
            "total_dollars": 14.14,
        },
    },
    # 5. ucp/get_cart — Google agent reading cart state
    {
        "ts": "2026-02-26T10:01:18Z",
        "protocol": "ucp",
        "endpoint": "/ucp/get_cart",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 88.5,
        "request_body": {"cart_id": CART_ID},
        "response_body": {
            "status": "success",
            "cart_id": CART_ID,
            "items": [
                {
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "quantity": 1,
                    "unit_price_dollars": SAMPLE_PRODUCT["price"],
                }
            ],
            "item_count": 1,
        },
    },
]


# ─── ACP traces ──────────────────────────────────────────────────────────────

ACP_TRACES = [
    # 1. Create checkout session (ChatGPT/OpenAI agent adds item to checkout)
    {
        "ts": "2026-02-26T10:02:00Z",
        "protocol": "acp",
        "endpoint": "/acp/checkout-sessions",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 195.2,
        "request_body": {
            "line_items": [
                {
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "price_dollars": SAMPLE_PRODUCT["price"],
                    "quantity": 1,
                    "image_url": "https://example.com/dell-xps15.jpg",
                }
            ],
            "currency": "USD",
            "buyer_email": "alice@example.com",
        },
        "response_body": {
            "protocol": "acp",
            "id": ACP_SESSION_ID,
            "status": "pending",
            "line_items": [
                {
                    "id": "li-001",
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "quantity": 1,
                    "unit_price_cents": 129999,
                    "subtotal_cents": 129999,
                }
            ],
            "totals": {
                "subtotal_cents": 129999,
                "shipping_cents": 0,
                "tax_cents": 0,
                "total_cents": 129999,
            },
            "currency": "USD",
            "buyer": {"email": "alice@example.com"},
            "shipping_address": None,
            "shipping_method": "standard",
            "order_id": None,
        },
    },
    # 2. Get session
    {
        "ts": "2026-02-26T10:02:04Z",
        "protocol": "acp",
        "endpoint": f"/acp/checkout-sessions/{ACP_SESSION_ID}",
        "method": "GET",
        "status_code": 200,
        "duration_ms": 8.3,
        "request_body": None,
        "response_body": {
            "protocol": "acp",
            "id": ACP_SESSION_ID,
            "status": "pending",
            "line_items": [
                {
                    "id": "li-001",
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "quantity": 1,
                    "unit_price_cents": 129999,
                    "subtotal_cents": 129999,
                }
            ],
            "totals": {
                "subtotal_cents": 129999,
                "shipping_cents": 0,
                "tax_cents": 0,
                "total_cents": 129999,
            },
            "currency": "USD",
        },
    },
    # 3. Update session — add shipping address + method
    {
        "ts": "2026-02-26T10:02:08Z",
        "protocol": "acp",
        "endpoint": f"/acp/checkout-sessions/{ACP_SESSION_ID}",
        "method": "PUT",
        "status_code": 200,
        "duration_ms": 12.1,
        "request_body": {
            "buyer": {"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com"},
            "shipping_address": {
                "street": "450 Serra Mall",
                "city": "Stanford",
                "state": "CA",
                "postal_code": "94305",
                "country": "US",
            },
            "shipping_method": "express",
        },
        "response_body": {
            "protocol": "acp",
            "id": ACP_SESSION_ID,
            "status": "pending",
            "line_items": [
                {
                    "id": "li-001",
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "quantity": 1,
                    "unit_price_cents": 129999,
                    "subtotal_cents": 129999,
                }
            ],
            "totals": {
                "subtotal_cents": 129999,
                "shipping_cents": 1999,
                "tax_cents": 11375,   # 8.75% CA tax
                "total_cents": 143373,
            },
            "currency": "USD",
            "buyer": {"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com"},
            "shipping_address": {
                "street": "450 Serra Mall",
                "city": "Stanford",
                "state": "CA",
                "postal_code": "94305",
                "country": "US",
            },
            "shipping_method": "express",
        },
    },
    # 4. Complete checkout (payment token → order placed)
    {
        "ts": "2026-02-26T10:02:14Z",
        "protocol": "acp",
        "endpoint": f"/acp/checkout-sessions/{ACP_SESSION_ID}/complete",
        "method": "POST",
        "status_code": 200,
        "duration_ms": 312.8,
        "request_body": {
            "payment_token": "tok_visa_4242",
            "payment_method": "card",
        },
        "response_body": {
            "protocol": "acp",
            "id": ACP_SESSION_ID,
            "status": "completed",
            "order_id": ORDER_ID,
            "line_items": [
                {
                    "id": "li-001",
                    "product_id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "quantity": 1,
                    "unit_price_cents": 129999,
                    "subtotal_cents": 129999,
                }
            ],
            "totals": {
                "subtotal_cents": 129999,
                "shipping_cents": 1999,
                "tax_cents": 11375,
                "total_cents": 143373,
            },
            "currency": "USD",
        },
    },
    # 5. Product feed (ChatGPT pulls catalog for product discovery)
    {
        "ts": "2026-02-26T10:02:20Z",
        "protocol": "acp",
        "endpoint": "/acp/feed.json",
        "method": "GET",
        "status_code": 200,
        "duration_ms": 248.5,
        "request_body": None,
        "response_body": {
            "protocol": "acp",
            "count": 2,
            "items": [
                {
                    "id": SAMPLE_PRODUCT["product_id"],
                    "title": SAMPLE_PRODUCT["name"],
                    "description": "Powerful 15.6\" laptop with Intel i7 and 16GB RAM.",
                    "price_dollars": SAMPLE_PRODUCT["price"],
                    "currency": "USD",
                    "availability": "in_stock",
                    "inventory": SAMPLE_PRODUCT["inventory"],
                    "image_url": "https://example.com/dell-xps15.jpg",
                    "product_url": "/products/" + SAMPLE_PRODUCT["product_id"],
                    "category": "Electronics",
                    "brand": "Dell",
                    "rating": 4.6,
                },
                {
                    "id": SAMPLE_PRODUCT_2["product_id"],
                    "title": SAMPLE_PRODUCT_2["name"],
                    "description": "14\" MacBook Pro with Apple M3 Pro chip.",
                    "price_dollars": SAMPLE_PRODUCT_2["price"],
                    "currency": "USD",
                    "availability": "in_stock",
                    "inventory": SAMPLE_PRODUCT_2["inventory"],
                    "image_url": "https://example.com/macbook-m3.jpg",
                    "product_url": "/products/" + SAMPLE_PRODUCT_2["product_id"],
                    "category": "Electronics",
                    "brand": "Apple",
                    "rating": 4.8,
                },
            ],
        },
    },
]


# ─── Write files ──────────────────────────────────────────────────────────────

def write_traces(name: str, traces: list, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for trace in traces:
            f.write(json.dumps(trace) + "\n")
    print(f"Wrote {len(traces)} trace entries → {path}")


def main() -> None:
    write_traces("MCP", MCP_TRACES, LOGS_DIR / "mcp_traces.jsonl")
    write_traces("UCP", UCP_TRACES, LOGS_DIR / "ucp_traces.jsonl")
    write_traces("ACP", ACP_TRACES, LOGS_DIR / "acp_traces.jsonl")

    print()
    print("Protocol trace files for Hannah:")
    print(f"  {LOGS_DIR}/mcp_traces.jsonl  — {len(MCP_TRACES)} MCP (Anthropic tool-call) interactions")
    print(f"  {LOGS_DIR}/ucp_traces.jsonl  — {len(UCP_TRACES)} UCP (Google commerce adapter) interactions")
    print(f"  {LOGS_DIR}/acp_traces.jsonl  — {len(ACP_TRACES)} ACP (OpenAI/Stripe checkout-session) interactions")
    print()
    print("Each entry: { ts, protocol, endpoint, method, status_code, duration_ms,")
    print("              request_body, response_body }")


if __name__ == "__main__":
    main()
