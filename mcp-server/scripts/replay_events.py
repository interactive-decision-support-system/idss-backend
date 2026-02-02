#!/usr/bin/env python3
"""
Replay MCP events for deterministic testing and evaluation.

This script reads events from the mcp_events table and replays them
against the MCP server to verify deterministic behavior.

Usage:
    python scripts/replay_events.py --tool search_products --limit 10
    python scripts/replay_events.py --request-id abc123
    python scripts/replay_events.py --session-id session-123
    python scripts/replay_events.py --since 2024-01-20 --until 2024-01-21
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import requests
import os

# Database connection
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://julih@localhost:5432/mcp_ecommerce"
)

# MCP server URL
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")

# Create database session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_events(
    tool_name: str = None,
    request_id: str = None,
    session_id: str = None,
    since: str = None,
    until: str = None,
    limit: int = 100
):
    """
    Query events from mcp_events table.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT 
                event_id, timestamp, request_id, session_id, tool_name, endpoint_path,
                input_summary, outcome_status, constraints_count,
                latency_ms, cache_hit, timings_breakdown, sources,
                product_ids, cart_id, order_id, response_summary
            FROM mcp_events
            WHERE 1=1
        """)
        
        params = {}
        
        if tool_name:
            query = text(str(query) + " AND tool_name = :tool_name")
            params['tool_name'] = tool_name
        
        if request_id:
            query = text(str(query) + " AND request_id = :request_id")
            params['request_id'] = request_id
        
        if session_id:
            query = text(str(query) + " AND session_id = :session_id")
            params['session_id'] = session_id
        
        if since:
            query = text(str(query) + " AND timestamp >= :since")
            params['since'] = since
        
        if until:
            query = text(str(query) + " AND timestamp <= :until")
            params['until'] = until
        
        query = text(str(query) + " ORDER BY timestamp DESC LIMIT :limit")
        params['limit'] = limit
        
        result = db.execute(query, params)
        events = []
        
        for row in result:
            events.append({
                'event_id': row.event_id,
                'timestamp': row.timestamp.isoformat() if row.timestamp else None,
                'request_id': row.request_id,
                'session_id': row.session_id,
                'tool_name': row.tool_name,
                'endpoint_path': row.endpoint_path,
                'input_summary': json.loads(row.input_summary) if row.input_summary else {},
                'outcome_status': row.outcome_status,
                'constraints_count': row.constraints_count,
                'latency_ms': float(row.latency_ms) if row.latency_ms else None,
                'cache_hit': row.cache_hit,
                'timings_breakdown': json.loads(row.timings_breakdown) if row.timings_breakdown else {},
                'sources': row.sources,
                'product_ids': row.product_ids,
                'cart_id': row.cart_id,
                'order_id': row.order_id,
                'response_summary': json.loads(row.response_summary) if row.response_summary else {}
            })
        
        return events
    
    finally:
        db.close()


def replay_event(event: dict, verify: bool = True) -> dict:
    """
    Replay a single event against the MCP server.
    
    Returns:
        dict with 'success', 'original_status', 'replay_status', 'match', 'latency_diff'
    """
    tool_name = event['tool_name']
    endpoint_path = event['endpoint_path']
    input_summary = event['input_summary']
    original_status = event['outcome_status']
    original_latency = event['latency_ms']
    
    # Reconstruct request from input_summary
    # Note: This is a simplified reconstruction - full replay would need original request
    request_data = {}
    if 'query' in input_summary:
        request_data['query'] = input_summary['query']
    if 'filters' in input_summary:
        request_data['filters'] = input_summary['filters']
    if 'product_id' in input_summary:
        request_data['product_id'] = input_summary['product_id']
    if 'cart_id' in input_summary:
        request_data['cart_id'] = input_summary['cart_id']
    if 'qty' in input_summary:
        request_data['qty'] = input_summary['qty']
    if 'limit' in input_summary:
        request_data['limit'] = input_summary['limit']
    if 'payment_method_id' in input_summary:
        request_data['payment_method_id'] = input_summary['payment_method_id']
    if 'address_id' in input_summary:
        request_data['address_id'] = input_summary['address_id']
    
    # Make request to MCP server
    url = f"{MCP_SERVER_URL}{endpoint_path}"
    
    try:
        import time
        start_time = time.time()
        
        response = requests.post(url, json=request_data, timeout=30)
        replay_latency = (time.time() - start_time) * 1000
        
        if response.status_code == 200:
            replay_data = response.json()
            replay_status = replay_data.get('status')
            
            match = (replay_status == original_status) if verify else None
            latency_diff = replay_latency - original_latency if original_latency else None
            
            return {
                'success': True,
                'original_status': original_status,
                'replay_status': replay_status,
                'match': match,
                'original_latency_ms': original_latency,
                'replay_latency_ms': replay_latency,
                'latency_diff_ms': latency_diff,
                'response': replay_data
            }
        else:
            return {
                'success': False,
                'error': f"HTTP {response.status_code}: {response.text}",
                'original_status': original_status
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'original_status': original_status
        }


def main():
    parser = argparse.ArgumentParser(description='Replay MCP events for deterministic testing')
    parser.add_argument('--tool', help='Filter by tool name (search_products, get_product, etc.)')
    parser.add_argument('--request-id', help='Filter by specific request ID')
    parser.add_argument('--session-id', help='Filter by session ID')
    parser.add_argument('--since', help='Filter events since this timestamp (ISO format)')
    parser.add_argument('--until', help='Filter events until this timestamp (ISO format)')
    parser.add_argument('--limit', type=int, default=100, help='Maximum number of events to replay')
    parser.add_argument('--verify', action='store_true', default=True, help='Verify replay matches original')
    parser.add_argument('--output', help='Output file for replay results (JSON)')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("MCP Event Replay")
    print("=" * 70)
    print(f"Querying events...")
    
    # Get events
    events = get_events(
        tool_name=args.tool,
        request_id=args.request_id,
        session_id=args.session_id,
        since=args.since,
        until=args.until,
        limit=args.limit
    )
    
    print(f"Found {len(events)} events to replay")
    print()
    
    if not events:
        print("No events found matching criteria.")
        return
    
    # Replay events
    results = []
    matches = 0
    mismatches = 0
    errors = 0
    
    for i, event in enumerate(events, 1):
        print(f"[{i}/{len(events)}] Replaying {event['tool_name']} (request_id: {event['request_id'][:8]}...)")
        
        result = replay_event(event, verify=args.verify)
        result['event'] = event
        results.append(result)
        
        if result['success']:
            if result.get('match'):
                matches += 1
                print(f"  [OK] Match: {result['replay_status']}")
            elif result.get('match') is False:
                mismatches += 1
                print(f"  [FAIL] Mismatch: {result['original_status']} != {result['replay_status']}")
            else:
                print(f"  [WARN]  Replayed: {result['replay_status']} (verification disabled)")
        else:
            errors += 1
            print(f"  [FAIL] Error: {result.get('error', 'Unknown error')}")
    
    print()
    print("=" * 70)
    print("Replay Summary")
    print("=" * 70)
    print(f"Total events: {len(events)}")
    if args.verify:
        print(f"Matches: {matches}")
        print(f"Mismatches: {mismatches}")
    print(f"Errors: {errors}")
    print()
    
    # Save results if output file specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to {args.output}")


if __name__ == '__main__':
    main()
