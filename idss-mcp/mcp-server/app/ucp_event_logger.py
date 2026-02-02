"""
Event logging helper for UCP endpoints.

UCP endpoints have different response structures than MCP endpoints,
so we need a separate logging helper.
"""

import uuid
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from app.event_logger import log_event
from app.schemas import ResponseStatus


def log_ucp_event(
    db: Session,
    tool_name: str,
    endpoint_path: str,
    request_data: Any,
    response: Any,
    session_id: Optional[str] = None
) -> None:
    """
    Log UCP endpoint events for research replay.
    
    Converts UCP response format to MCP-compatible format for logging.
    """
    try:
        request_id = str(uuid.uuid4())
        
        # Convert request to dict
        if hasattr(request_data, 'model_dump'):
            req_dict = request_data.model_dump()
        elif hasattr(request_data, 'dict'):
            req_dict = request_data.dict()
        elif isinstance(request_data, dict):
            req_dict = request_data
        else:
            req_dict = {"raw": str(request_data)}
        
        # Convert response to dict
        if hasattr(response, 'model_dump'):
            resp_dict = response.model_dump()
        elif hasattr(response, 'dict'):
            resp_dict = response.dict()
        elif isinstance(response, dict):
            resp_dict = response
        else:
            resp_dict = {"raw": str(response)}
        
        # Map UCP status to MCP ResponseStatus
        ucp_status = resp_dict.get("status", "error")
        if ucp_status == "success":
            mcp_status = ResponseStatus.OK
        elif ucp_status == "error":
            mcp_status = ResponseStatus.ERROR
        else:
            mcp_status = ResponseStatus.ERROR
        
        # Create trace structure
        trace = {
            "request_id": request_id,
            "cache_hit": False,  # UCP endpoints don't use cache directly
            "timings_ms": {},
            "sources": ["ucp"]
        }
        
        # Create version info
        version = {
            "catalog_version": "1.0.0",
            "updated_at": None
        }
        
        # Log the event
        log_event(
            db=db,
            request_id=request_id,
            tool_name=tool_name,
            endpoint_path=endpoint_path,
            request_data=req_dict,
            response_status=mcp_status,
            response_data=resp_dict,
            trace=trace,
            version=version,
            session_id=session_id
        )
    
    except Exception as e:
        # Don't fail the request if logging fails
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to log UCP event: {e}", exc_info=True)
