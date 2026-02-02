#!/usr/bin/env python3
"""
Compact MCP events into a versioned snapshot JSON.

This script reads events from mcp_events table and creates a compact
snapshot JSON file for offline benchmarking and analysis.

Usage:
    python scripts/compact_snapshot.py
    python scripts/compact_snapshot.py --since 2024-01-20 --until 2024-01-21
    python scripts/compact_snapshot.py --output snapshots/events_v20240120.json
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
import os

# Database connection
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://julih@localhost:5432/mcp_ecommerce"
)

# Create database session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_events_summary(since: str = None, until: str = None):
    """
    Get aggregated summary of events.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT 
                tool_name,
                outcome_status,
                COUNT(*) as count,
                AVG(latency_ms) as avg_latency_ms,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms) as p50_latency_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_latency_ms,
                SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) as cache_hits,
                COUNT(*) - SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) as cache_misses
            FROM mcp_events
            WHERE 1=1
        """)
        
        params = {}
        
        if since:
            query = text(str(query) + " AND timestamp >= :since")
            params['since'] = since
        
        if until:
            query = text(str(query) + " AND timestamp <= :until")
            params['until'] = until
        
        query = text(str(query) + " GROUP BY tool_name, outcome_status ORDER BY tool_name, outcome_status")
        
        result = db.execute(query, params)
        
        summary = []
        for row in result:
            summary.append({
                'tool_name': row.tool_name,
                'outcome_status': row.outcome_status,
                'count': row.count,
                'avg_latency_ms': float(row.avg_latency_ms) if row.avg_latency_ms else None,
                'p50_latency_ms': float(row.p50_latency_ms) if row.p50_latency_ms else None,
                'p95_latency_ms': float(row.p95_latency_ms) if row.p95_latency_ms else None,
                'cache_hits': row.cache_hits,
                'cache_misses': row.cache_misses,
                'cache_hit_rate': float(row.cache_hits) / row.count if row.count > 0 else 0.0
            })
        
        return summary
    
    finally:
        db.close()


def get_sample_events(tool_name: str = None, limit: int = 100):
    """
    Get sample events for replay.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT 
                request_id, timestamp, tool_name, endpoint_path,
                input_summary, outcome_status, latency_ms, cache_hit
            FROM mcp_events
            WHERE 1=1
        """)
        
        params = {}
        
        if tool_name:
            query = text(str(query) + " AND tool_name = :tool_name")
            params['tool_name'] = tool_name
        
        query = text(str(query) + " ORDER BY timestamp DESC LIMIT :limit")
        params['limit'] = limit
        
        result = db.execute(query, params)
        
        events = []
        for row in result:
            events.append({
                'request_id': row.request_id,
                'timestamp': row.timestamp.isoformat() if row.timestamp else None,
                'tool_name': row.tool_name,
                'endpoint_path': row.endpoint_path,
                'input_summary': json.loads(row.input_summary) if row.input_summary else {},
                'outcome_status': row.outcome_status,
                'latency_ms': float(row.latency_ms) if row.latency_ms else None,
                'cache_hit': row.cache_hit
            })
        
        return events
    
    finally:
        db.close()


def create_snapshot(since: str = None, until: str = None, output_file: str = None):
    """
    Create a compact snapshot from events.
    """
    timestamp = datetime.utcnow()
    snapshot_version = timestamp.strftime("v%Y%m%d_%H%M%S")
    
    print("=" * 70)
    print("MCP Event Snapshot Compaction")
    print("=" * 70)
    print(f"Creating snapshot: {snapshot_version}")
    print()
    
    # Get aggregated summary
    print("Aggregating event statistics...")
    summary = get_events_summary(since=since, until=until)
    
    # Get sample events for replay
    print("Collecting sample events...")
    sample_events = {}
    for tool in ['search_products', 'get_product', 'add_to_cart', 'checkout']:
        sample_events[tool] = get_sample_events(tool_name=tool, limit=50)
    
    # Create snapshot
    snapshot = {
        'schema_version': '1.0.0',
        'snapshot_version': snapshot_version,
        'generated_at': timestamp.isoformat(),
        'time_range': {
            'since': since,
            'until': until
        },
        'summary': summary,
        'sample_events': sample_events,
        'metadata': {
            'total_events': sum(s['count'] for s in summary),
            'tools': list(set(s['tool_name'] for s in summary)),
            'statuses': list(set(s['outcome_status'] for s in summary))
        }
    }
    
    # Determine output file
    if not output_file:
        snapshots_dir = Path(__file__).parent.parent / "snapshots"
        snapshots_dir.mkdir(exist_ok=True)
        output_file = snapshots_dir / f"events_{snapshot_version}.json"
    
    # Write snapshot
    print(f"Writing snapshot to {output_file}...")
    with open(output_file, 'w') as f:
        json.dump(snapshot, f, indent=2, default=str)
    
    print()
    print("=" * 70)
    print("Snapshot Created")
    print("=" * 70)
    print(f"File: {output_file}")
    print(f"Total events: {snapshot['metadata']['total_events']}")
    print(f"Tools: {', '.join(snapshot['metadata']['tools'])}")
    print()
    
    return output_file


def main():
    parser = argparse.ArgumentParser(description='Compact MCP events into snapshot JSON')
    parser.add_argument('--since', help='Filter events since this timestamp (ISO format)')
    parser.add_argument('--until', help='Filter events until this timestamp (ISO format)')
    parser.add_argument('--output', help='Output file path')
    
    args = parser.parse_args()
    
    create_snapshot(
        since=args.since,
        until=args.until,
        output_file=args.output
    )


if __name__ == '__main__':
    main()
