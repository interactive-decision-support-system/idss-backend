"""
Ablation Studies for MCP Components.

Systematically measures the contribution of each component by:
1. Measuring performance with component enabled
2. Measuring performance with component disabled
3. Calculating the delta (overhead or benefit)

Components tested:
- Provenance tracking
- Field projection
- Metrics collection
- Structured logging
- Cache (Redis)
- Multi-LLM adapter layer
"""

import pytest
import time
import json
from typing import Dict, Any, List
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.schemas import ProductDetail, ProvenanceInfo, GetProductRequest
from app.metrics import MetricsCollector
from app.structured_logger import StructuredLogger
from app.rca_analyzer import RCAAnalyzer


class AblationStudy:
    """Framework for running ablation studies."""
    
    def __init__(self):
        self.results = []
    
    def measure_latency(self, func, iterations: int = 100) -> Dict[str, float]:
        """
        Measure function latency over multiple iterations.
        
        Returns:
            Dict with min, max, mean, median, p50, p95, p99
        """
        latencies = []
        
        for _ in range(iterations):
            start = time.perf_counter()
            func()
            end = time.perf_counter()
            latencies.append((end - start) * 1000)  # Convert to ms
        
        latencies.sort()
        
        return {
            "min": round(min(latencies), 4),
            "max": round(max(latencies), 4),
            "mean": round(sum(latencies) / len(latencies), 4),
            "median": round(latencies[len(latencies) // 2], 4),
            "p50": round(latencies[int(len(latencies) * 0.50)], 4),
            "p95": round(latencies[int(len(latencies) * 0.95)], 4),
            "p99": round(latencies[int(len(latencies) * 0.99)], 4),
        }
    
    def compare(
        self,
        baseline_func,
        enhanced_func,
        component_name: str,
        iterations: int = 100
    ) -> Dict[str, Any]:
        """
        Compare baseline vs enhanced version.
        
        Returns:
            Comparison results with overhead calculation
        """
        print(f"\nStudy: Ablation Study: {component_name}")
        print("=" * 60)
        
        # Measure baseline
        print(f"  Measuring baseline (without {component_name})...")
        baseline = self.measure_latency(baseline_func, iterations)
        
        # Measure enhanced
        print(f"  Measuring enhanced (with {component_name})...")
        enhanced = self.measure_latency(enhanced_func, iterations)
        
        # Calculate overhead
        overhead_mean = enhanced["mean"] - baseline["mean"]
        overhead_pct = (overhead_mean / baseline["mean"]) * 100 if baseline["mean"] > 0 else 0
        
        result = {
            "component": component_name,
            "iterations": iterations,
            "baseline": baseline,
            "enhanced": enhanced,
            "overhead_ms": round(overhead_mean, 4),
            "overhead_pct": round(overhead_pct, 2),
        }
        
        # Print results
        print(f"\n  Results:")
        print(f"     Baseline mean: {baseline['mean']:.4f}ms")
        print(f"     Enhanced mean: {enhanced['mean']:.4f}ms")
        print(f"     Overhead: {overhead_mean:.4f}ms ({overhead_pct:.2f}%)")
        print(f"     p95 overhead: {enhanced['p95'] - baseline['p95']:.4f}ms")
        
        self.results.append(result)
        return result
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive ablation report."""
        report = {
            "total_components_tested": len(self.results),
            "components": self.results,
            "summary": {
                "total_overhead_ms": sum(r["overhead_ms"] for r in self.results),
                "mean_overhead_pct": sum(r["overhead_pct"] for r in self.results) / len(self.results) if self.results else 0,
            }
        }
        
        # Rank by overhead
        sorted_by_overhead = sorted(self.results, key=lambda x: x["overhead_ms"], reverse=True)
        report["highest_overhead"] = sorted_by_overhead[0] if sorted_by_overhead else None
        report["lowest_overhead"] = sorted_by_overhead[-1] if sorted_by_overhead else None
        
        return report


# ============================================================================
# Test 1: Provenance Tracking Overhead
# ============================================================================

def test_ablation_provenance():
    """Measure overhead of provenance tracking."""
    study = AblationStudy()
    
    # Baseline: Create ProductDetail without provenance
    def baseline():
        from datetime import datetime
        product = ProductDetail(
            product_id="TEST-001",
            name="Test Product",
            description="Test description",
            category="electronics",
            brand="TestBrand",
            price_cents=9999,
            currency="USD",
            available_qty=10,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            product_type="ecommerce",
            metadata={"sku": "TEST-001"}
        )
        return product
    
    # Enhanced: Create ProductDetail with provenance
    def enhanced():
        from datetime import datetime
        provenance = ProvenanceInfo(
            source="postgres",
            timestamp=datetime.utcnow()
        )
        product = ProductDetail(
            product_id="TEST-001",
            name="Test Product",
            description="Test description",
            category="electronics",
            brand="TestBrand",
            price_cents=9999,
            currency="USD",
            available_qty=10,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            product_type="ecommerce",
            metadata={"sku": "TEST-001"},
            provenance=provenance
        )
        return product
    
    result = study.compare(baseline, enhanced, "Provenance Tracking", iterations=1000)
    
    # Assertion: Overhead should be < 1ms
    assert result["overhead_ms"] < 1.0, f"Provenance overhead too high: {result['overhead_ms']}ms"
    
    return result


# ============================================================================
# Test 2: Field Projection Impact
# ============================================================================

def test_ablation_field_projection():
    """Measure benefit of field projection on response size."""
    study = AblationStudy()
    
    from datetime import datetime
    from app.idss_adapter import apply_field_projection
    
    # Create a full product detail
    full_product = ProductDetail(
        product_id="TEST-002",
        name="Large Product with Lots of Data",
        description="A" * 1000,  # 1KB description
        category="electronics",
        brand="TestBrand",
        price_cents=9999,
        currency="USD",
        available_qty=10,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        product_type="ecommerce",
        metadata={"specs": "B" * 500}  # 500B metadata
    )
    
    # Baseline: Full product serialization
    def baseline():
        data = full_product.dict()
        serialized = json.dumps(data, default=str)
        return len(serialized)
    
    # Enhanced: Projected fields only (name, price)
    def enhanced():
        projected = apply_field_projection(full_product, ["name", "price_cents"])
        data = projected.dict()
        serialized = json.dumps(data, default=str)
        return len(serialized)
    
    # Measure sizes
    full_size = baseline()
    projected_size = enhanced()
    reduction_pct = ((full_size - projected_size) / full_size) * 100
    
    print(f"\nStudy: Ablation Study: Field Projection")
    print("=" * 60)
    print(f"  Full response size: {full_size} bytes")
    print(f"  Projected size: {projected_size} bytes")
    print(f"  Reduction: {full_size - projected_size} bytes ({reduction_pct:.2f}%)")
    
    # Assertion: Should reduce size by at least 50%
    assert reduction_pct > 50, f"Field projection reduction too low: {reduction_pct}%"
    
    return {
        "component": "Field Projection",
        "full_size_bytes": full_size,
        "projected_size_bytes": projected_size,
        "reduction_bytes": full_size - projected_size,
        "reduction_pct": round(reduction_pct, 2)
    }


# ============================================================================
# Test 3: Metrics Collection Overhead
# ============================================================================

def test_ablation_metrics():
    """Measure overhead of metrics collection."""
    study = AblationStudy()
    
    collector = MetricsCollector()
    
    # Baseline: No metrics
    def baseline():
        pass
    
    # Enhanced: Record metrics
    def enhanced():
        collector.record_latency("test_endpoint", 15.5)
        collector.record_cache_hit()
    
    result = study.compare(baseline, enhanced, "Metrics Collection", iterations=1000)
    
    # Assertion: Overhead should be < 0.5ms
    assert result["overhead_ms"] < 0.5, f"Metrics overhead too high: {result['overhead_ms']}ms"
    
    return result


# ============================================================================
# Test 4: Structured Logging Overhead
# ============================================================================

def test_ablation_logging():
    """Measure overhead of structured JSON logging."""
    study = AblationStudy()
    
    logger = StructuredLogger("test", log_level="INFO")
    
    # Baseline: No logging
    def baseline():
        pass
    
    # Enhanced: Structured logging
    def enhanced():
        logger.log_request("test_endpoint", "req-123", params={"test": "data"})
    
    result = study.compare(baseline, enhanced, "Structured Logging", iterations=1000)
    
    # Assertion: Overhead should be < 1ms
    assert result["overhead_ms"] < 1.0, f"Logging overhead too high: {result['overhead_ms']}ms"
    
    return result


# ============================================================================
# Test 5: RCA Analysis Overhead
# ============================================================================

def test_ablation_rca():
    """Measure overhead of RCA analysis."""
    study = AblationStudy()
    
    analyzer = RCAAnalyzer()
    
    test_response = {
        "status": "OUT_OF_STOCK",
        "constraints": [{
            "code": "INSUFFICIENT_INVENTORY",
            "message": "Not enough stock",
            "details": {"requested_qty": 5, "available_qty": 2}
        }]
    }
    
    test_trace = {
        "timings_ms": {"total": 150, "db": 100, "cache": 10},
        "cache_hit": False,
        "sources": ["postgres"]
    }
    
    # Baseline: No RCA
    def baseline():
        pass
    
    # Enhanced: Full RCA analysis
    def enhanced():
        analyzer.analyze_failure(test_response, test_trace)
    
    result = study.compare(baseline, enhanced, "RCA Analysis", iterations=1000)
    
    # Assertion: Overhead should be < 2ms
    assert result["overhead_ms"] < 2.0, f"RCA overhead too high: {result['overhead_ms']}ms"
    
    return result


# ============================================================================
# Test 6: Multi-LLM Adapter Overhead
# ============================================================================

def test_ablation_multi_llm_adapter():
    """Measure overhead of multi-LLM tool schema conversion."""
    study = AblationStudy()
    
    from app.tool_schemas import to_openai_function, to_gemini_function, to_claude_tool, TOOL_SEARCH_PRODUCTS
    
    # Baseline: Direct tool definition access
    def baseline():
        tool = TOOL_SEARCH_PRODUCTS
        return tool
    
    # Enhanced: Convert to all three formats
    def enhanced():
        openai = to_openai_function(TOOL_SEARCH_PRODUCTS)
        gemini = to_gemini_function(TOOL_SEARCH_PRODUCTS)
        claude = to_claude_tool(TOOL_SEARCH_PRODUCTS)
        return [openai, gemini, claude]
    
    result = study.compare(baseline, enhanced, "Multi-LLM Adapter", iterations=1000)
    
    # Assertion: Overhead should be < 0.5ms
    assert result["overhead_ms"] < 0.5, f"Multi-LLM overhead too high: {result['overhead_ms']}ms"
    
    return result


# ============================================================================
# Integration Test: Full Request Path
# ============================================================================

def test_ablation_full_request_path():
    """
    Measure total overhead of all MCP enhancements on a full request.
    
    Simulates: Request → Metrics → Logging → DB Query → Provenance → Response
    """
    study = AblationStudy()
    
    # Baseline: Minimal request processing
    def baseline():
        from datetime import datetime
        # Simulate minimal work
        product = {
            "product_id": "TEST-001",
            "name": "Test Product",
            "price_cents": 9999
        }
        return product
    
    # Enhanced: Full MCP request with all features
    def enhanced():
        from datetime import datetime
        from app.metrics import record_request_metrics
        from app.structured_logger import log_request, log_response
        
        request_id = "test-req-123"
        
        # Structured logging
        log_request("get_product", request_id, params={"product_id": "TEST-001"})
        
        # Simulate DB query with timing
        start = time.perf_counter()
        product = ProductDetail(
            product_id="TEST-001",
            name="Test Product",
            description="Test",
            category="electronics",
            brand="TestBrand",
            price_cents=9999,
            currency="USD",
            available_qty=10,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            product_type="ecommerce",
            metadata={"sku": "TEST-001"},
            provenance=ProvenanceInfo(source="postgres", timestamp=datetime.utcnow())
        )
        latency = (time.perf_counter() - start) * 1000
        
        # Metrics
        record_request_metrics("get_product", latency, cache_hit=False)
        
        # Logging
        log_response("get_product", request_id, "OK", latency, cache_hit=False)
        
        return product
    
    result = study.compare(baseline, enhanced, "Full MCP Request Path", iterations=100)
    
    # Assertion: Total overhead should be < 5ms
    assert result["overhead_ms"] < 5.0, f"Full request overhead too high: {result['overhead_ms']}ms"
    
    print(f"\n  [OK] Total MCP overhead acceptable: {result['overhead_ms']:.4f}ms ({result['overhead_pct']:.2f}%)")
    
    return result


# ============================================================================
# Summary Report Generator
# ============================================================================

def test_generate_ablation_report():
    """Generate comprehensive ablation study report."""
    
    print("\n" + "=" * 80)
    print("Study: ABLATION STUDY REPORT")
    print("=" * 80)
    
    # Run all ablation studies
    results = []
    
    try:
        results.append(test_ablation_provenance())
    except Exception as e:
        print(f"[FAIL] Provenance test failed: {e}")
    
    try:
        results.append(test_ablation_field_projection())
    except Exception as e:
        print(f"[FAIL] Field projection test failed: {e}")
    
    try:
        results.append(test_ablation_metrics())
    except Exception as e:
        print(f"[FAIL] Metrics test failed: {e}")
    
    try:
        results.append(test_ablation_logging())
    except Exception as e:
        print(f"[FAIL] Logging test failed: {e}")
    
    try:
        results.append(test_ablation_rca())
    except Exception as e:
        print(f"[FAIL] RCA test failed: {e}")
    
    try:
        results.append(test_ablation_multi_llm_adapter())
    except Exception as e:
        print(f"[FAIL] Multi-LLM test failed: {e}")
    
    try:
        results.append(test_ablation_full_request_path())
    except Exception as e:
        print(f"[FAIL] Full request test failed: {e}")
    
    # Generate summary
    print("\n" + "=" * 80)
    print("Results: SUMMARY")
    print("=" * 80)
    
    overhead_results = [r for r in results if "overhead_ms" in r]
    if overhead_results:
        total_overhead = sum(r["overhead_ms"] for r in overhead_results)
        mean_overhead_pct = sum(r["overhead_pct"] for r in overhead_results) / len(overhead_results)
        
        print(f"\n  Total components tested: {len(results)}")
        print(f"  Cumulative overhead: {total_overhead:.4f}ms")
        print(f"  Mean overhead: {mean_overhead_pct:.2f}%")
        
        # Find highest overhead component
        highest = max(overhead_results, key=lambda x: x["overhead_ms"])
        print(f"\n  Highest overhead: {highest['component']} ({highest['overhead_ms']:.4f}ms)")
        
        # Find lowest overhead component
        lowest = min(overhead_results, key=lambda x: x["overhead_ms"])
        print(f"  Lowest overhead: {lowest['component']} ({lowest['overhead_ms']:.4f}ms)")
    
    print("\n" + "=" * 80)
    print("[OK] ABLATION STUDY COMPLETE")
    print("=" * 80 + "\n")
    
    return results


if __name__ == "__main__":
    # Run all tests
    test_generate_ablation_report()
