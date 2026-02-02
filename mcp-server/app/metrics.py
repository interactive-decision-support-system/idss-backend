"""
Observability Metrics for Research-Grade MCP.

Tracks:
- Latency percentiles (p50, p95, p99)
- Cache hit rates
- Request counts per endpoint
- Error rates
"""

import time
from typing import Dict, List, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
import statistics


class MetricsCollector:
    """
    In-memory metrics collector for observability.
    
    For production, this would integrate with Prometheus/StatsD.
    For research, provides basic percentile and rate tracking.
    """
    
    def __init__(self, window_size: int = 1000):
        """
        Initialize metrics collector.
        
        Args:
            window_size: Number of recent samples to keep for percentiles
        """
        self.window_size = window_size
        
        # Latency tracking (sliding window)
        self.latencies: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        
        # Cache metrics
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Request counters
        self.request_counts: Dict[str, int] = defaultdict(int)
        self.error_counts: Dict[str, int] = defaultdict(int)
        
        # Timestamp tracking
        self.start_time = datetime.utcnow()
        self.last_reset = datetime.utcnow()
    
    def record_latency(self, endpoint: str, latency_ms: float):
        """Record a latency sample for an endpoint."""
        self.latencies[endpoint].append(latency_ms)
        self.request_counts[endpoint] += 1
    
    def record_cache_hit(self):
        """Record a cache hit."""
        self.cache_hits += 1
    
    def record_cache_miss(self):
        """Record a cache miss."""
        self.cache_misses += 1
    
    def record_error(self, endpoint: str):
        """Record an error for an endpoint."""
        self.error_counts[endpoint] += 1
    
    def get_percentile(self, endpoint: str, percentile: float) -> Optional[float]:
        """
        Get a latency percentile for an endpoint.
        
        Args:
            endpoint: Endpoint name
            percentile: Percentile (0-100)
        
        Returns:
            Latency in ms, or None if insufficient data
        """
        if endpoint not in self.latencies or len(self.latencies[endpoint]) == 0:
            return None
        
        values = sorted(self.latencies[endpoint])
        if len(values) < 10:  # Need at least 10 samples for meaningful percentiles
            return None
        
        index = int(len(values) * (percentile / 100.0))
        index = min(index, len(values) - 1)
        return values[index]
    
    def get_cache_hit_rate(self) -> float:
        """Get the cache hit rate as a percentage."""
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return (self.cache_hits / total) * 100.0
    
    def get_error_rate(self, endpoint: str) -> float:
        """Get the error rate for an endpoint as a percentage."""
        total_requests = self.request_counts[endpoint]
        if total_requests == 0:
            return 0.0
        errors = self.error_counts[endpoint]
        return (errors / total_requests) * 100.0
    
    def get_summary(self) -> Dict:
        """
        Get a summary of all metrics.
        
        Returns:
            Dict with metrics summary for all endpoints
        """
        uptime_seconds = (datetime.utcnow() - self.start_time).total_seconds()
        
        summary = {
            "uptime_seconds": uptime_seconds,
            "cache": {
                "hit_rate_pct": round(self.get_cache_hit_rate(), 2),
                "total_hits": self.cache_hits,
                "total_misses": self.cache_misses
            },
            "endpoints": {}
        }
        
        # Add per-endpoint metrics
        for endpoint in self.request_counts.keys():
            p50 = self.get_percentile(endpoint, 50)
            p95 = self.get_percentile(endpoint, 95)
            p99 = self.get_percentile(endpoint, 99)
            
            endpoint_metrics = {
                "total_requests": self.request_counts[endpoint],
                "total_errors": self.error_counts[endpoint],
                "error_rate_pct": round(self.get_error_rate(endpoint), 2),
            }
            
            if p50 is not None:
                endpoint_metrics["latency_p50_ms"] = round(p50, 2)
            if p95 is not None:
                endpoint_metrics["latency_p95_ms"] = round(p95, 2)
            if p99 is not None:
                endpoint_metrics["latency_p99_ms"] = round(p99, 2)
            
            # Add average latency
            if len(self.latencies[endpoint]) > 0:
                endpoint_metrics["latency_avg_ms"] = round(
                    statistics.mean(self.latencies[endpoint]), 2
                )
            
            summary["endpoints"][endpoint] = endpoint_metrics
        
        return summary
    
    def reset(self):
        """Reset all metrics (useful for testing)."""
        self.latencies.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        self.request_counts.clear()
        self.error_counts.clear()
        self.last_reset = datetime.utcnow()


# Global metrics collector instance
metrics_collector = MetricsCollector()


def record_request_metrics(
    endpoint: str,
    latency_ms: float,
    cache_hit: bool,
    is_error: bool = False
):
    """
    Convenience function to record all request metrics at once.
    
    Args:
        endpoint: Endpoint name (e.g. "search_products", "get_product")
        latency_ms: Total request latency in milliseconds
        cache_hit: Whether this was a cache hit
        is_error: Whether this request resulted in an error
    """
    metrics_collector.record_latency(endpoint, latency_ms)
    
    if cache_hit:
        metrics_collector.record_cache_hit()
    else:
        metrics_collector.record_cache_miss()
    
    if is_error:
        metrics_collector.record_error(endpoint)
