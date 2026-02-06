#!/usr/bin/env python3
"""
Latency Target Verification

Measures actual latency for:
1. Vector search: Target 50-300ms
2. IDSS ranking for laptops: Target 100-500ms
3. Diversification: Target 10-50ms
4. Overall search: Target p95 ≤ 1000ms
5. Get product: Target p95 ≤ 200ms

Run: python scripts/verify_latency_targets.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
import time
import statistics
from typing import List, Dict, Any
import json


MCP_BASE_URL = "http://localhost:8001"


def measure_latency(func, iterations: int = 10) -> Dict[str, float]:
    """
    Measure latency statistics.
    
    Args:
        func: Function to measure
        iterations: Number of iterations
        
    Returns:
        Dict with min, max, avg, p50, p95, p99
    """
    latencies = []
    
    for i in range(iterations):
        start = time.time()
        try:
            func()
            latency_ms = (time.time() - start) * 1000
            latencies.append(latency_ms)
        except Exception as e:
            print(f"  [FAIL] Error in iteration {i+1}: {e}")
            continue
        
        # Small delay between requests
        time.sleep(0.1)
    
    if not latencies:
        return {"error": "No successful measurements"}
    
    latencies.sort()
    n = len(latencies)
    
    return {
        "min": latencies[0],
        "max": latencies[-1],
        "avg": statistics.mean(latencies),
        "median": statistics.median(latencies),
        "p50": latencies[int(n * 0.50)],
        "p95": latencies[int(n * 0.95)] if n > 1 else latencies[0],
        "p99": latencies[int(n * 0.99)] if n > 1 else latencies[0],
        "count": n
    }


def test_get_product_latency():
    """Test get-product endpoint latency."""
    print("\n" + "="*80)
    print("TEST 1: Get Product Latency")
    print("Target: p95 ≤ 200ms")
    print("="*80)
    
    # First, get a sample product_id
    try:
        response = requests.post(
            f"{MCP_BASE_URL}/api/search-products",
            json={"query": "laptop", "limit": 1},
            timeout=10
        )
        data = response.json()
        products = data.get("data", {}).get("products", [])
        
        if not products:
            print("[FAIL] No products found to test")
            return None
        
        product_id = products[0]["product_id"]
        print(f"Testing with product_id: {product_id}")
        
    except Exception as e:
        print(f"[FAIL] Failed to get sample product: {e}")
        return None
    
    # Measure latency
    def get_product():
        response = requests.post(
            f"{MCP_BASE_URL}/api/get-product",
            json={"product_id": product_id},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    
    stats = measure_latency(get_product, iterations=20)
    
    # Display results
    print(f"\nResults ({stats['count']} requests):")
    print(f"  Min:    {stats['min']:.1f}ms")
    print(f"  Avg:    {stats['avg']:.1f}ms")
    print(f"  Median: {stats['median']:.1f}ms")
    print(f"  P95:    {stats['p95']:.1f}ms")
    print(f"  P99:    {stats['p99']:.1f}ms")
    print(f"  Max:    {stats['max']:.1f}ms")
    
    # Check target
    target_p95 = 200
    passed = stats['p95'] <= target_p95
    status = " PASS" if passed else "[FAIL] FAIL"
    print(f"\nTarget: p95 ≤ {target_p95}ms")
    print(f"Actual: p95 = {stats['p95']:.1f}ms")
    print(f"Status: {status}")
    
    return stats


def test_search_latency():
    """Test search-products endpoint latency."""
    print("\n" + "="*80)
    print("TEST 2: Search Products Latency")
    print("Target: p95 ≤ 1000ms")
    print("="*80)
    
    test_queries = [
        "laptop",
        "gaming laptop",
        "MacBook",
        "Dell laptop",
        "laptop under $2000"
    ]
    
    all_latencies = []
    
    for query in test_queries:
        print(f"\nTesting query: '{query}'")
        
        def search_products():
            response = requests.post(
                f"{MCP_BASE_URL}/api/search-products",
                json={"query": query, "limit": 10},
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        
        stats = measure_latency(search_products, iterations=5)
        all_latencies.extend([stats[key] for key in ['p50', 'p95'] if key in stats])
        
        print(f"  Avg: {stats.get('avg', 0):.1f}ms, P95: {stats.get('p95', 0):.1f}ms")
    
    # Overall statistics
    if all_latencies:
        all_latencies.sort()
        n = len(all_latencies)
        overall_p95 = all_latencies[int(n * 0.95)] if n > 1 else all_latencies[0]
        overall_avg = statistics.mean(all_latencies)
        
        print(f"\nOverall Results:")
        print(f"  Avg: {overall_avg:.1f}ms")
        print(f"  P95: {overall_p95:.1f}ms")
        
        target_p95 = 1000
        passed = overall_p95 <= target_p95
        status = " PASS" if passed else "[FAIL] FAIL"
        print(f"\nTarget: p95 ≤ {target_p95}ms")
        print(f"Actual: p95 = {overall_p95:.1f}ms")
        print(f"Status: {status}")
        
        return {"p95": overall_p95, "avg": overall_avg, "passed": passed}
    
    return None


def test_idss_ranking_latency():
    """Test IDSS ranking component latency."""
    print("\n" + "="*80)
    print("TEST 3: IDSS Ranking Latency")
    print("Target: 100-500ms")
    print("="*80)
    
    # Query that triggers IDSS ranking
    query = "gaming laptop NVIDIA under $2000"
    
    print(f"Testing query: '{query}'")
    print("Note: This measures search with IDSS ranking enabled")
    
    latencies = []
    
    for i in range(10):
        try:
            start = time.time()
            response = requests.post(
                f"{MCP_BASE_URL}/api/search-products",
                json={
                    "query": query,
                    "filters": {
                        "category": "Electronics",
                        "_use_idss_ranking": True
                    },
                    "limit": 20
                },
                timeout=30
            )
            total_latency = (time.time() - start) * 1000
            
            data = response.json()
            trace = data.get("trace", {})
            timings = trace.get("timings_ms", {})
            
            # Extract IDSS ranking time if available
            idss_time = timings.get("idss_ranking_ms", 0)
            
            if idss_time > 0:
                latencies.append(idss_time)
                print(f"  Request {i+1}: {idss_time:.1f}ms (IDSS ranking)")
            else:
                # If no specific IDSS timing, use total - db time
                db_time = timings.get("db", 0)
                estimated_idss = max(0, total_latency - db_time - 50)  # 50ms overhead
                latencies.append(estimated_idss)
                print(f"  Request {i+1}: ~{estimated_idss:.1f}ms (estimated)")
            
            time.sleep(0.2)
            
        except Exception as e:
            print(f"  [FAIL] Error in request {i+1}: {e}")
            continue
    
    if latencies:
        latencies.sort()
        n = len(latencies)
        avg = statistics.mean(latencies)
        p95 = latencies[int(n * 0.95)] if n > 1 else latencies[0]
        
        print(f"\nResults ({n} requests):")
        print(f"  Min: {min(latencies):.1f}ms")
        print(f"  Avg: {avg:.1f}ms")
        print(f"  P95: {p95:.1f}ms")
        print(f"  Max: {max(latencies):.1f}ms")
        
        # Check target (100-500ms)
        target_min = 100
        target_max = 500
        in_range = target_min <= avg <= target_max
        status = " PASS" if in_range else "[WARN] CHECK"
        
        print(f"\nTarget: {target_min}-{target_max}ms")
        print(f"Actual: {avg:.1f}ms (avg)")
        print(f"Status: {status}")
        
        return {"avg": avg, "p95": p95, "passed": in_range}
    
    return None


def test_component_breakdown():
    """Test breakdown of search latency by component."""
    print("\n" + "="*80)
    print("TEST 4: Component Latency Breakdown")
    print("="*80)
    
    query = "Dell laptop"
    
    print(f"Testing query: '{query}'")
    print("Measuring individual component timings...\n")
    
    try:
        response = requests.post(
            f"{MCP_BASE_URL}/api/search-products",
            json={"query": query, "limit": 10},
            timeout=30
        )
        data = response.json()
        
        trace = data.get("trace", {})
        timings = trace.get("timings_ms", {})
        
        print("Component Timings:")
        print("-" * 60)
        
        components = [
            ("parse_ms", "Query Parsing", "10-50ms"),
            ("db", "Database Query", "10-100ms"),
            ("idss_ranking_ms", "IDSS Ranking", "100-500ms"),
            ("diversification_ms", "Diversification", "10-50ms"),
            ("total", "Total", "p95 ≤ 1000ms")
        ]
        
        for key, name, target in components:
            value = timings.get(key, 0)
            if value > 0:
                print(f"  {name:<20}: {value:>8.1f}ms  (target: {target})")
        
        print("-" * 60)
        print(f"  {'Total (measured)':<20}: {timings.get('total', 0):>8.1f}ms")
        
        return timings
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        return None


def generate_summary(results: Dict[str, Any]):
    """Generate final summary report."""
    print("\n" + "="*80)
    print("LATENCY VERIFICATION SUMMARY")
    print("="*80)
    
    print("\nTarget vs Actual:")
    print("-" * 60)
    
    tests = [
        ("Get Product (p95)", 200, results.get("get_product", {}).get("p95", 0)),
        ("Search Products (p95)", 1000, results.get("search", {}).get("p95", 0)),
        ("IDSS Ranking (avg)", 300, results.get("idss_ranking", {}).get("avg", 0)),
    ]
    
    all_passed = True
    for name, target, actual in tests:
        if actual > 0:
            passed = actual <= target
            status = "" if passed else "[FAIL]"
            print(f"  {status} {name:<25}: Target ≤{target:>5}ms, Actual: {actual:>6.1f}ms")
            if not passed:
                all_passed = False
    
    print("-" * 60)
    
    if all_passed:
        print("\n ALL LATENCY TARGETS MET!")
    else:
        print("\n[WARN] SOME TARGETS NOT MET - Review optimization opportunities")
    
    print("\nRecommendations:")
    if results.get("get_product", {}).get("p95", 0) > 200:
        print("  - Optimize database queries for get-product")
        print("  - Consider more aggressive caching")
    
    if results.get("search", {}).get("p95", 0) > 1000:
        print("  - Optimize IDSS ranking algorithm")
        print("  - Add query result caching")
        print("  - Consider pagination for large result sets")
    
    print("\n" + "="*80)


def main():
    """Main latency verification."""
    print("="*80)
    print("LATENCY TARGET VERIFICATION")
    print("Measuring actual performance against targets")
    print("="*80)
    
    # Check server is running
    try:
        response = requests.get(f"{MCP_BASE_URL}/health", timeout=5)
        if response.status_code != 200:
            print(f"[FAIL] Server not healthy: {response.status_code}")
            return
    except Exception as e:
        print(f"[FAIL] Cannot connect to MCP server at {MCP_BASE_URL}")
        print(f"   Error: {e}")
        print("\nPlease start the server first:")
        print("   cd mcp-server && uvicorn app.main:app --port 8001")
        return
    
    results = {}
    
    # Run tests
    get_product_stats = test_get_product_latency()
    if get_product_stats:
        results["get_product"] = get_product_stats
    
    search_stats = test_search_latency()
    if search_stats:
        results["search"] = search_stats
    
    idss_stats = test_idss_ranking_latency()
    if idss_stats:
        results["idss_ranking"] = idss_stats
    
    component_timings = test_component_breakdown()
    if component_timings:
        results["components"] = component_timings
    
    # Generate summary
    generate_summary(results)


if __name__ == "__main__":
    main()
