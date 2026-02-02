"""
Structured JSON Logger for Research-Grade MCP.

Logs all events in structured JSON format for:
- Research analysis
- Debugging
- Audit trails
- Performance analysis
"""

import json
import logging
import sys
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum


class LogLevel(str, Enum):
    """Log levels matching Python logging."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StructuredLogger:
    """
    JSON structured logger for MCP events.
    
    Each log entry includes:
    - timestamp (ISO 8601)
    - level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
    - event_type (request, response, error, cache_hit, etc.)
    - message (human-readable)
    - context (structured data: request_id, endpoint, etc.)
    """
    
    def __init__(self, name: str = "mcp", log_level: str = "INFO"):
        """
        Initialize structured logger.
        
        Args:
            name: Logger name
            log_level: Minimum log level to output
        """
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level))
        
        # Create console handler with JSON formatter
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, log_level))
        
        # Remove default formatter - we'll format as JSON
        handler.setFormatter(logging.Formatter('%(message)s'))
        
        # Clear any existing handlers
        self.logger.handlers = []
        self.logger.addHandler(handler)
    
    def _log(
        self,
        level: LogLevel,
        event_type: str,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        Internal log method that formats and emits JSON.
        
        Args:
            level: Log level
            event_type: Event type (request, response, error, etc.)
            message: Human-readable message
            context: Additional structured context
        """
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level.value,
            "logger": self.name,
            "event_type": event_type,
            "message": message,
        }
        
        if context:
            log_entry["context"] = context
        
        # Emit as single-line JSON
        log_line = json.dumps(log_entry)
        
        # Route to appropriate log level
        if level == LogLevel.DEBUG:
            self.logger.debug(log_line)
        elif level == LogLevel.INFO:
            self.logger.info(log_line)
        elif level == LogLevel.WARNING:
            self.logger.warning(log_line)
        elif level == LogLevel.ERROR:
            self.logger.error(log_line)
        elif level == LogLevel.CRITICAL:
            self.logger.critical(log_line)
    
    # Convenience methods for each log level
    
    def debug(self, event_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log a DEBUG event."""
        self._log(LogLevel.DEBUG, event_type, message, context)
    
    def info(self, event_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log an INFO event."""
        self._log(LogLevel.INFO, event_type, message, context)
    
    def warning(self, event_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log a WARNING event."""
        self._log(LogLevel.WARNING, event_type, message, context)
    
    def error(self, event_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log an ERROR event."""
        self._log(LogLevel.ERROR, event_type, message, context)
    
    def critical(self, event_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log a CRITICAL event."""
        self._log(LogLevel.CRITICAL, event_type, message, context)
    
    # Specialized logging methods for common MCP events
    
    def log_request(
        self,
        endpoint: str,
        request_id: str,
        method: str = "POST",
        params: Optional[Dict[str, Any]] = None
    ):
        """Log an incoming request."""
        self.info(
            "request",
            f"{method} {endpoint}",
            {
                "request_id": request_id,
                "endpoint": endpoint,
                "method": method,
                "params": params or {}
            }
        )
    
    def log_response(
        self,
        endpoint: str,
        request_id: str,
        status: str,
        latency_ms: float,
        cache_hit: bool = False
    ):
        """Log a response."""
        self.info(
            "response",
            f"Response {status} for {endpoint}",
            {
                "request_id": request_id,
                "endpoint": endpoint,
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "cache_hit": cache_hit
            }
        )
    
    def log_cache_event(
        self,
        event: str,
        key: str,
        hit: bool,
        latency_ms: Optional[float] = None
    ):
        """Log a cache event."""
        context = {
            "cache_key": key,
            "cache_hit": hit
        }
        if latency_ms is not None:
            context["latency_ms"] = round(latency_ms, 2)
        
        self.debug(
            "cache",
            f"Cache {event}: {'HIT' if hit else 'MISS'}",
            context
        )
    
    def log_database_query(
        self,
        query_type: str,
        table: str,
        latency_ms: float,
        rows_affected: Optional[int] = None
    ):
        """Log a database query."""
        context = {
            "query_type": query_type,
            "table": table,
            "latency_ms": round(latency_ms, 2)
        }
        if rows_affected is not None:
            context["rows_affected"] = rows_affected
        
        self.debug(
            "database",
            f"{query_type} on {table}",
            context
        )
    
    def log_error(
        self,
        error_type: str,
        error_message: str,
        request_id: Optional[str] = None,
        stack_trace: Optional[str] = None
    ):
        """Log an error."""
        context = {
            "error_type": error_type,
            "error_message": error_message
        }
        if request_id:
            context["request_id"] = request_id
        if stack_trace:
            context["stack_trace"] = stack_trace
        
        self.error(
            "error",
            f"{error_type}: {error_message}",
            context
        )


# Global structured logger instance
structured_logger = StructuredLogger("mcp", log_level="INFO")


# Convenience functions for common logging patterns
def log_request(endpoint: str, request_id: str, **kwargs):
    """Log an incoming request."""
    structured_logger.log_request(endpoint, request_id, **kwargs)


def log_response(endpoint: str, request_id: str, status: str, latency_ms: float, **kwargs):
    """Log a response."""
    structured_logger.log_response(endpoint, request_id, status, latency_ms, **kwargs)


def log_error(error_type: str, error_message: str, **kwargs):
    """Log an error."""
    structured_logger.log_error(error_type, error_message, **kwargs)
