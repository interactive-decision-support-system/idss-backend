"""
Root Cause Analysis (RCA) for MCP Failures and Performance Issues.

Based on CS372 research methodology for LLM agent debugging.
Turns failures into measurable categories for research analysis.

Categories (from CS372):
1. Schema/Tool misuse: wrong field, missing required parameter
2. Grounding failure: agent mentioned attribute not in MCP response
3. Staleness: catalog_version mismatch, stale cache
4. Retrieval miss: KG returned irrelevant candidates
5. Execution constraint: out-of-stock, qty limit, shipping constraint
6. Latency root cause: cache miss vs DB slow vs KG slow
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum


class RCACategory(str, Enum):
    """Root cause failure categories for systematic analysis."""
    SCHEMA_MISUSE = "schema_misuse"
    GROUNDING_FAILURE = "grounding_failure"
    STALENESS = "staleness"
    RETRIEVAL_MISS = "retrieval_miss"
    EXECUTION_CONSTRAINT = "execution_constraint"
    LATENCY_ISSUE = "latency_issue"
    UNKNOWN = "unknown"


class RCASeverity(str, Enum):
    """Severity levels for failures."""
    CRITICAL = "critical"  # Transaction failed, user blocked
    HIGH = "high"          # Degraded experience, retry needed
    MEDIUM = "medium"      # Warning, suboptimal path
    LOW = "low"            # Info, optimization opportunity


class RCAAnalyzer:
    """
    Deterministic Root Cause Analysis for MCP failures.
    
    Uses structured outputs from MCP:
    - status + constraints (why rejected)
    - trace (cache/db/kg timings)
    - provenance + versions (db/kg/snapshot)
    
    Can be extended with LLM-based summarization for complex cases.
    """
    
    def __init__(self):
        """Initialize RCA analyzer with configurable thresholds."""
        # Latency thresholds (milliseconds)
        self.threshold_total = 1000    # >1s is slow
        self.threshold_db = 500        # >500ms DB is slow
        self.threshold_cache = 100     # >100ms cache is slow
        self.threshold_kg = 300        # >300ms KG is slow
    
    def analyze_failure(
        self,
        response: Dict[str, Any],
        trace: Optional[Dict[str, Any]] = None,
        event_log: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Analyze a failed or suboptimal request.
        
        Args:
            response: MCP response with status and constraints
            trace: Request trace with timings and sources
            event_log: Optional event history for context
        
        Returns:
            RCA result with category, evidence, suggested fix
        """
        status = response.get("status", "UNKNOWN")
        constraints = response.get("constraints", [])
        
        # Initialize result
        rca = {
            "category": RCACategory.UNKNOWN,
            "severity": RCASeverity.MEDIUM,
            "evidence": [],
            "suggested_fix": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "raw_status": status
        }
        
        # Analyze based on status code
        if status == "PRODUCT_NOT_FOUND" or status == "NOT_FOUND":
            return self._analyze_not_found(rca, response, trace)
        
        elif status == "OUT_OF_STOCK":
            return self._analyze_out_of_stock(rca, response, trace)
        
        elif status == "INVALID":
            return self._analyze_invalid(rca, response, constraints)
        
        elif status == "NEEDS_CLARIFICATION":
            return self._analyze_needs_clarification(rca, response, constraints)
        
        elif status == "OK":
            # Success, but check for performance issues
            if trace:
                return self._analyze_latency(rca, trace)
            else:
                rca["category"] = "success"
                rca["severity"] = RCASeverity.LOW
                return rca
        
        else:
            rca["category"] = RCACategory.UNKNOWN
            rca["evidence"].append(f"Unknown status: {status}")
            return rca
    
    def _analyze_not_found(
        self,
        rca: Dict[str, Any],
        response: Dict[str, Any],
        trace: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze NOT_FOUND failures."""
        rca["category"] = RCACategory.RETRIEVAL_MISS
        rca["severity"] = RCASeverity.MEDIUM
        
        # Extract product_id from constraints
        constraints = response.get("constraints", [])
        product_id = None
        for constraint in constraints:
            details = constraint.get("details", {})
            if "product_id" in details:
                product_id = details["product_id"]
                break
        
        rca["evidence"].append(f"Product {product_id} not found in database")
        
        if trace:
            sources = trace.get("sources", [])
            rca["evidence"].append(f"Searched sources: {', '.join(sources)}")
        
        rca["suggested_fix"] = [
            "Check if product was deleted or never existed",
            "Verify product_id format (e.g., VIN- prefix for vehicles)",
            "Check if correct backend is being queried",
            "Consider fuzzy matching for typos"
        ]
        
        return rca
    
    def _analyze_out_of_stock(
        self,
        rca: Dict[str, Any],
        response: Dict[str, Any],
        trace: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze OUT_OF_STOCK failures."""
        rca["category"] = RCACategory.EXECUTION_CONSTRAINT
        rca["severity"] = RCASeverity.HIGH
        
        constraints = response.get("constraints", [])
        for constraint in constraints:
            details = constraint.get("details", {})
            if "requested_qty" in details and "available_qty" in details:
                requested = details["requested_qty"]
                available = details["available_qty"]
                rca["evidence"].append(
                    f"Requested {requested} but only {available} available"
                )
        
        rca["suggested_fix"] = [
            "Reduce quantity to available amount",
            "Check inventory refresh frequency (cache staleness)",
            "Consider backorder or waitlist",
            "Suggest alternative products"
        ]
        
        return rca
    
    def _analyze_invalid(
        self,
        rca: Dict[str, Any],
        response: Dict[str, Any],
        constraints: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze INVALID status (schema/validation errors)."""
        rca["category"] = RCACategory.SCHEMA_MISUSE
        rca["severity"] = RCASeverity.HIGH
        
        for constraint in constraints:
            code = constraint.get("code", "UNKNOWN")
            message = constraint.get("message", "")
            
            rca["evidence"].append(f"{code}: {message}")
            
            # Check for specific schema issues
            if "required" in message.lower() or "missing" in message.lower():
                rca["evidence"].append("Missing required field")
            elif "forbidden" in message.lower() or "unknown" in message.lower():
                rca["evidence"].append("Extra/unknown field provided")
            elif "type" in message.lower():
                rca["evidence"].append("Field type mismatch")
        
        rca["suggested_fix"] = [
            "Review tool schema definition",
            "Check for typos in field names",
            "Verify parameter types match schema",
            "Use allowed_fields from constraint"
        ]
        
        return rca
    
    def _analyze_needs_clarification(
        self,
        rca: Dict[str, Any],
        response: Dict[str, Any],
        constraints: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze NEEDS_CLARIFICATION (ambiguous queries)."""
        rca["category"] = RCACategory.GROUNDING_FAILURE
        rca["severity"] = RCASeverity.MEDIUM
        
        rca["evidence"].append("Query was ambiguous or underspecified")
        
        for constraint in constraints:
            allowed_fields = constraint.get("allowed_fields", [])
            if allowed_fields:
                rca["evidence"].append(
                    f"Missing clarification on: {', '.join(allowed_fields)}"
                )
        
        rca["suggested_fix"] = [
            "Use suggested_actions from constraint",
            "Implement Socratic clarification questions",
            "Prompt agent to ask user for missing details"
        ]
        
        return rca
    
    def _analyze_latency(
        self,
        rca: Dict[str, Any],
        trace: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze latency issues even on successful requests."""
        timings = trace.get("timings_ms", {})
        total = timings.get("total", 0)
        
        if total < self.threshold_total:
            # No latency issue
            rca["category"] = "success"
            rca["severity"] = RCASeverity.LOW
            return rca
        
        rca["category"] = RCACategory.LATENCY_ISSUE
        rca["severity"] = RCASeverity.MEDIUM if total < 2000 else RCASeverity.HIGH
        
        rca["evidence"].append(f"Total latency: {total:.2f}ms (threshold: {self.threshold_total}ms)")
        
        # Identify bottleneck
        db_time = timings.get("db", 0)
        cache_time = timings.get("cache", 0)
        kg_time = timings.get("kg", 0)
        
        bottlenecks = []
        if db_time > self.threshold_db:
            bottlenecks.append(f"DB slow ({db_time:.2f}ms)")
            rca["evidence"].append(f"Database query took {db_time:.2f}ms")
        
        if cache_time > self.threshold_cache:
            bottlenecks.append(f"Cache slow ({cache_time:.2f}ms)")
            rca["evidence"].append(f"Cache lookup took {cache_time:.2f}ms")
        
        if kg_time > self.threshold_kg:
            bottlenecks.append(f"KG slow ({kg_time:.2f}ms)")
            rca["evidence"].append(f"Knowledge graph query took {kg_time:.2f}ms")
        
        if bottlenecks:
            rca["suggested_fix"] = [
                f"Optimize {bottleneck}" for bottleneck in bottlenecks
            ]
        else:
            rca["suggested_fix"] = [
                "Check network latency",
                "Profile MCP adapter overhead",
                "Consider connection pooling"
            ]
        
        # Check cache effectiveness
        cache_hit = trace.get("cache_hit", False)
        if not cache_hit:
            rca["evidence"].append("Cache miss - had to query database")
            rca["suggested_fix"].append("Improve cache hit rate (increase TTL or preload)")
        
        return rca
    
    def generate_report(
        self,
        rca_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate aggregated RCA report from multiple failures.
        
        Args:
            rca_results: List of RCA analysis results
        
        Returns:
            Aggregated report with top causes, metrics, visualizations
        """
        report = {
            "total_analyzed": len(rca_results),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "category_breakdown": {},
            "severity_breakdown": {},
            "top_failures": [],
            "metrics": {}
        }
        
        # Count by category
        for rca in rca_results:
            category = rca.get("category", RCACategory.UNKNOWN)
            severity = rca.get("severity", RCASeverity.MEDIUM)
            
            report["category_breakdown"][category] = \
                report["category_breakdown"].get(category, 0) + 1
            
            report["severity_breakdown"][severity] = \
                report["severity_breakdown"].get(severity, 0) + 1
        
        # Calculate percentages
        total = len(rca_results)
        for category, count in report["category_breakdown"].items():
            pct = (count / total) * 100
            report["metrics"][f"{category}_pct"] = round(pct, 2)
        
        # Identify top 5 failure types
        sorted_categories = sorted(
            report["category_breakdown"].items(),
            key=lambda x: x[1],
            reverse=True
        )
        report["top_failures"] = [
            {"category": cat, "count": count, "percentage": round((count/total)*100, 2)}
            for cat, count in sorted_categories[:5]
        ]
        
        return report


# Global RCA analyzer instance
rca_analyzer = RCAAnalyzer()


def analyze_request(response: Dict[str, Any], trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Convenience function to analyze a single request.
    
    Args:
        response: MCP response
        trace: Request trace
    
    Returns:
        RCA result
    """
    return rca_analyzer.analyze_failure(response, trace)
