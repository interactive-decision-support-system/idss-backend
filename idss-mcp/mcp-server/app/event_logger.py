"""
Event logging for MCP requests/responses.
Append-only log for research replay and debugging.
"""

import hashlib
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models import Base
from app.schemas import ResponseStatus


def hash_input(input_data: Dict[str, Any]) -> str:
    """
    Create SHA-256 hash of input for deduplication.
    Redacts sensitive fields before hashing.
    """
    # Create a copy and redact sensitive fields
    redacted = redact_sensitive_data(input_data.copy())
    
    # Convert to JSON string and hash
    json_str = json.dumps(redacted, sort_keys=True)
    return hashlib.sha256(json_str.encode()).hexdigest()


def redact_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Redact sensitive data from input/output for logging.
    Removes: credit cards, addresses, payment info, personal data.
    """
    redacted = {}
    sensitive_keys = [
        'payment_method_id', 'address_id', 'credit_card', 'cvv', 'ssn',
        'password', 'token', 'api_key', 'secret', 'address', 'zip_code',
        'phone', 'email'  # Can include if needed, but usually redact
    ]
    
    for key, value in data.items():
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in sensitive_keys):
            redacted[key] = '[REDACTED]'
        elif isinstance(value, dict):
            redacted[key] = redact_sensitive_data(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_sensitive_data(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            redacted[key] = value
    
    return redacted


def create_input_summary(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a redacted summary of the input request.
    """
    redacted = redact_sensitive_data(request_data)
    
    # Create summary with key fields
    summary = {}
    if 'query' in redacted:
        summary['query'] = redacted['query'][:100] if redacted['query'] else None  # Truncate long queries
    if 'filters' in redacted:
        summary['filters'] = redacted['filters']
    if 'product_id' in redacted:
        summary['product_id'] = redacted['product_id']
    if 'cart_id' in redacted:
        summary['cart_id'] = redacted['cart_id']
    if 'limit' in redacted:
        summary['limit'] = redacted['limit']
    
    return summary


def create_response_summary(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a redacted summary of the response.
    """
    redacted = redact_sensitive_data(response_data)
    
    # Create summary with key fields
    summary = {}
    if 'status' in redacted:
        summary['status'] = redacted['status']
    if 'constraints' in redacted:
        summary['constraints_count'] = len(redacted.get('constraints', []))
        # Include constraint codes but not full details
        summary['constraint_codes'] = [
            c.get('code') for c in redacted.get('constraints', [])
            if isinstance(c, dict) and 'code' in c
        ]
    if 'data' in redacted:
        data = redacted['data']
        if isinstance(data, dict):
            # Include counts but not full data
            if 'products' in data:
                summary['products_count'] = len(data['products']) if isinstance(data['products'], list) else 0
            if 'cart_id' in data:
                summary['cart_id'] = data['cart_id']
            if 'order_id' in data:
                summary['order_id'] = data['order_id']
    
    return summary


def extract_product_ids(request_data: Dict[str, Any], response_data: Dict[str, Any]) -> List[str]:
    """
    Extract product IDs from request and response.
    """
    product_ids = []
    
    # From request
    if 'product_id' in request_data:
        product_ids.append(request_data['product_id'])
    if 'filters' in request_data and 'product_ids' in request_data['filters']:
        product_ids.extend(request_data['filters']['product_ids'])
    
    # From response
    if 'data' in response_data:
        data = response_data['data']
        if isinstance(data, dict):
            if 'products' in data and isinstance(data['products'], list):
                for product in data['products']:
                    if isinstance(product, dict) and 'product_id' in product:
                        product_ids.append(product['product_id'])
            if 'product_id' in data:
                product_ids.append(data['product_id'])
            if 'items' in data and isinstance(data['items'], list):
                for item in data['items']:
                    if isinstance(item, dict) and 'product_id' in item:
                        product_ids.append(item['product_id'])
    
    # Remove duplicates and return
    return list(set(product_ids))


def log_event(
    db: Session,
    request_id: str,
    tool_name: str,
    endpoint_path: str,
    request_data: Dict[str, Any],
    response_status: ResponseStatus,
    response_data: Dict[str, Any],
    trace: Dict[str, Any],
    version: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None
) -> None:
    """
    Log an MCP event to the append-only event log.
    
    Args:
        db: Database session
        request_id: Unique request identifier
        tool_name: Name of the tool/endpoint (e.g., 'search_products')
        endpoint_path: API endpoint path
        request_data: Request payload (will be redacted)
        response_status: Response status code
        response_data: Response payload (will be redacted)
        trace: Request trace with timings
        version: Version information
        session_id: Optional session identifier
    """
    try:
        # Create input/output summaries
        input_summary = create_input_summary(request_data)
        response_summary = create_response_summary(response_data)
        
        # Extract product IDs
        product_ids = extract_product_ids(request_data, response_data)
        
        # Extract cart_id and order_id
        cart_id = request_data.get('cart_id') or response_data.get('data', {}).get('cart_id')
        order_id = response_data.get('data', {}).get('order_id')
        
        # Create input hash for deduplication
        input_hash = hash_input(request_data)
        
        # Extract timing information
        timings = trace.get('timings_ms', {})
        latency_ms = timings.get('total', 0)
        cache_hit = trace.get('cache_hit', False)
        sources = trace.get('sources', [])
        
        # Extract version info
        catalog_version = None
        db_version = None
        if version:
            catalog_version = version.get('catalog_version')
            db_version = version.get('db_version')
        
        # Count constraints
        constraints_count = len(response_data.get('constraints', []))
        
        # Insert event using raw SQL for append-only guarantee
        # (SQLAlchemy models can be updated, raw SQL ensures append-only)
        insert_sql = text("""
            INSERT INTO mcp_events (
                timestamp, request_id, session_id, tool_name, endpoint_path,
                input_hash, input_summary, outcome_status, constraints_count,
                latency_ms, cache_hit, timings_breakdown, sources,
                product_ids, cart_id, order_id, response_summary,
                catalog_version, db_version
            ) VALUES (
                :timestamp, :request_id, :session_id, :tool_name, :endpoint_path,
                :input_hash, :input_summary, :outcome_status, :constraints_count,
                :latency_ms, :cache_hit, :timings_breakdown, :sources,
                :product_ids, :cart_id, :order_id, :response_summary,
                :catalog_version, :db_version
            )
        """)
        
        db.execute(insert_sql, {
            'timestamp': datetime.utcnow(),
            'request_id': request_id,
            'session_id': session_id,
            'tool_name': tool_name,
            'endpoint_path': endpoint_path,
            'input_hash': input_hash,
            'input_summary': json.dumps(input_summary),
            'outcome_status': response_status.value,
            'constraints_count': constraints_count,
            'latency_ms': float(latency_ms) if latency_ms else None,
            'cache_hit': cache_hit,
            'timings_breakdown': json.dumps(timings),
            'sources': sources,
            'product_ids': product_ids,
            'cart_id': cart_id,
            'order_id': order_id,
            'response_summary': json.dumps(response_summary),
            'catalog_version': catalog_version,
            'db_version': db_version
        })
        
        db.commit()
        
    except Exception as e:
        # Don't fail the request if logging fails
        # Just log the error and continue
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to log event: {e}", exc_info=True)
        db.rollback()  # Rollback the failed insert, but don't affect the main transaction
