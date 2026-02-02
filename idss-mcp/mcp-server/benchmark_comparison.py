"""
Performance & Accuracy Benchmark: Direct IDSS vs MCP Adapter

This script compares:
1. Direct IDSS Backend (baseline)
2. Through MCP Adapter (overhead measurement)

Metrics:
- Latency (response time)
- Accuracy (data preservation)
- Data completeness
- Throughput
- Overhead percentage

Usage:
    python benchmark_comparison.py
"""

import asyncio
import time
import httpx
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.idss_adapter import search_products_idss
from app.schemas import SearchProductsRequest


# ============================================================================
# Configuration
# ============================================================================

IDSS_URL = "http://localhost:8000"
MCP_URL = "http://localhost:8001"  # If MCP server is running

# Test queries
TEST_QUERIES = [
    "affordable sedan",
    "luxury SUV",
    "fuel efficient car",
    "family minivan",
    "sporty coupe",
]

NUM_ITERATIONS = 5  # Iterations per query for averaging


# ============================================================================
# Direct IDSS Testing
# ============================================================================

async def test_direct_idss(query: str) -> Dict[str, Any]:
    """Test direct call to IDSS backend (baseline)."""
    start_time = time.time()
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{IDSS_URL}/chat",
                json={"message": query}
            )
            response.raise_for_status()
            data = response.json()
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        # Extract vehicle count
        vehicle_count = 0
        if data.get("response_type") == "recommendations" and data.get("recommendations"):
            # IDSS returns 2D grid
            for row in data["recommendations"]:
                if isinstance(row, list):
                    vehicle_count += len(row)
                else:
                    vehicle_count += 1
        
        return {
            "success": True,
            "latency_ms": elapsed_ms,
            "vehicle_count": vehicle_count,
            "response_type": data.get("response_type"),
            "has_grid": "recommendations" in data,
            "raw_response": data
        }
        
    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        return {
            "success": False,
            "latency_ms": elapsed_ms,
            "error": str(e),
            "vehicle_count": 0
        }


# ============================================================================
# MCP Adapter Testing
# ============================================================================

async def test_mcp_adapter(query: str) -> Dict[str, Any]:
    """Test call through MCP adapter (measures overhead)."""
    start_time = time.time()
    
    try:
        request = SearchProductsRequest(query=query, limit=100)
        response = await search_products_idss(request)
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        return {
            "success": True,
            "latency_ms": elapsed_ms,
            "product_count": len(response.data.products) if response.data else 0,
            "total_count": response.data.total_count if response.data else 0,
            "status": response.status,
            "has_metadata": any(p.metadata for p in response.data.products) if response.data and response.data.products else False,
            "has_product_type": any(p.product_type for p in response.data.products) if response.data and response.data.products else False,
            "trace": response.trace.timings_ms if response.trace else None,
            "raw_response": response
        }
        
    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        return {
            "success": False,
            "latency_ms": elapsed_ms,
            "error": str(e),
            "product_count": 0
        }


# ============================================================================
# Accuracy Testing
# ============================================================================

async def test_data_preservation(query: str) -> Dict[str, Any]:
    """Test that MCP preserves all data from IDSS."""
    
    # Get both responses
    direct = await test_direct_idss(query)
    mcp = await test_mcp_adapter(query)
    
    if not (direct["success"] and mcp["success"]):
        return {
            "data_preserved": False,
            "reason": "One or both requests failed"
        }
    
    # Compare counts
    direct_count = direct["vehicle_count"]
    mcp_count = mcp["product_count"]
    
    # Extract first vehicle from IDSS
    direct_vehicles = []
    if direct.get("raw_response", {}).get("recommendations"):
        for row in direct["raw_response"]["recommendations"]:
            if isinstance(row, list):
                direct_vehicles.extend(row)
            else:
                direct_vehicles.append(row)
    
    # Extract first product from MCP
    mcp_products = []
    if mcp.get("raw_response") and mcp["raw_response"].data:
        mcp_products = mcp["raw_response"].data.products
    
    # Compare first item if available
    data_match = True
    details = {}
    
    if direct_vehicles and mcp_products:
        direct_first = direct_vehicles[0]
        mcp_first = mcp_products[0]
        
        # Extract from nested structure
        if "vehicle" in direct_first:
            v = direct_first["vehicle"]
            retail = direct_first.get("retailListing", {})
        else:
            v = direct_first
            retail = direct_first
        
        # Check if key data preserved
        details = {
            "make_preserved": v.get("make") == mcp_first.brand,
            "price_match": abs((retail.get("price", 0) * 100) - mcp_first.price_cents) < 1,
            "metadata_added": mcp_first.metadata is not None,
            "product_type_added": mcp_first.product_type == "vehicle",
            "vin_in_id": "VIN-" in mcp_first.product_id if mcp_first.product_id else False
        }
    
    return {
        "count_match": direct_count == mcp_count,
        "direct_count": direct_count,
        "mcp_count": mcp_count,
        "data_preserved": all(details.values()) if details else None,
        "details": details
    }


# ============================================================================
# Benchmark Runner
# ============================================================================

async def run_benchmark():
    """Run comprehensive benchmark."""
    
    print("\n" + "=" * 80)
    print("PERFORMANCE & ACCURACY BENCHMARK: DIRECT IDSS vs MCP ADAPTER")
    print("=" * 80)
    print(f"\nTest Configuration:")
    print(f"  Queries: {len(TEST_QUERIES)}")
    print(f"  Iterations per query: {NUM_ITERATIONS}")
    print(f"  Total tests: {len(TEST_QUERIES) * NUM_ITERATIONS * 2}")
    print("\n" + "=" * 80)
    
    # Storage for results
    direct_latencies = []
    mcp_latencies = []
    accuracy_results = []
    
    # Run tests
    for i, query in enumerate(TEST_QUERIES, 1):
        print(f"\n📊 Test {i}/{len(TEST_QUERIES)}: '{query}'")
        print("-" * 80)
        
        # Run multiple iterations
        direct_times = []
        mcp_times = []
        
        for iteration in range(NUM_ITERATIONS):
            # Test direct IDSS
            direct_result = await test_direct_idss(query)
            if direct_result["success"]:
                direct_times.append(direct_result["latency_ms"])
                direct_latencies.append(direct_result["latency_ms"])
            
            # Small delay between tests
            await asyncio.sleep(0.5)
            
            # Test MCP adapter
            mcp_result = await test_mcp_adapter(query)
            if mcp_result["success"]:
                mcp_times.append(mcp_result["latency_ms"])
                mcp_latencies.append(mcp_result["latency_ms"])
            
            await asyncio.sleep(0.5)
        
        # Calculate averages
        if direct_times and mcp_times:
            direct_avg = statistics.mean(direct_times)
            mcp_avg = statistics.mean(mcp_times)
            overhead = mcp_avg - direct_avg
            overhead_pct = (overhead / direct_avg) * 100
            
            print(f"\n  Latency Results ({NUM_ITERATIONS} iterations):")
            print(f"    Direct IDSS:  {direct_avg:7.2f}ms (avg)  {min(direct_times):7.2f}ms (min)  {max(direct_times):7.2f}ms (max)")
            print(f"    MCP Adapter:  {mcp_avg:7.2f}ms (avg)  {min(mcp_times):7.2f}ms (min)  {max(mcp_times):7.2f}ms (max)")
            print(f"    Overhead:     {overhead:7.2f}ms ({overhead_pct:+.1f}%)")
            
            # Test data preservation
            accuracy = await test_data_preservation(query)
            accuracy_results.append(accuracy)
            
            print(f"\n  Data Preservation:")
            print(f"    Count match:      {'✅' if accuracy['count_match'] else '❌'} ({accuracy['direct_count']} IDSS → {accuracy['mcp_count']} MCP)")
            if accuracy.get("details"):
                print(f"    Make preserved:   {'✅' if accuracy['details']['make_preserved'] else '❌'}")
                print(f"    Price accurate:   {'✅' if accuracy['details']['price_match'] else '❌'}")
                print(f"    Metadata added:   {'✅' if accuracy['details']['metadata_added'] else '❌'}")
                print(f"    Product type:     {'✅' if accuracy['details']['product_type_added'] else '❌'}")
                print(f"    VIN in ID:        {'✅' if accuracy['details']['vin_in_id'] else '❌'}")
    
    # Overall Statistics
    print("\n" + "=" * 80)
    print("OVERALL BENCHMARK RESULTS")
    print("=" * 80)
    
    if direct_latencies and mcp_latencies:
        direct_mean = statistics.mean(direct_latencies)
        direct_median = statistics.median(direct_latencies)
        direct_stdev = statistics.stdev(direct_latencies) if len(direct_latencies) > 1 else 0
        
        mcp_mean = statistics.mean(mcp_latencies)
        mcp_median = statistics.median(mcp_latencies)
        mcp_stdev = statistics.stdev(mcp_latencies) if len(mcp_latencies) > 1 else 0
        
        overhead_mean = mcp_mean - direct_mean
        overhead_pct = (overhead_mean / direct_mean) * 100
        
        print(f"\n📈 Latency Statistics (n={len(direct_latencies)}):")
        print(f"\n  Direct IDSS Backend:")
        print(f"    Mean:     {direct_mean:7.2f}ms")
        print(f"    Median:   {direct_median:7.2f}ms")
        print(f"    Std Dev:  {direct_stdev:7.2f}ms")
        print(f"    Min:      {min(direct_latencies):7.2f}ms")
        print(f"    Max:      {max(direct_latencies):7.2f}ms")
        
        print(f"\n  Through MCP Adapter:")
        print(f"    Mean:     {mcp_mean:7.2f}ms")
        print(f"    Median:   {mcp_median:7.2f}ms")
        print(f"    Std Dev:  {mcp_stdev:7.2f}ms")
        print(f"    Min:      {min(mcp_latencies):7.2f}ms")
        print(f"    Max:      {max(mcp_latencies):7.2f}ms")
        
        print(f"\n  MCP Overhead:")
        print(f"    Mean:     {overhead_mean:7.2f}ms ({overhead_pct:+.1f}%)")
        print(f"    Analysis: {'✅ Acceptable' if overhead_pct < 20 else '⚠️  High' if overhead_pct < 50 else '❌ Very High'}")
    
    # Accuracy Summary
    if accuracy_results:
        count_matches = sum(1 for r in accuracy_results if r.get('count_match'))
        data_preserved = sum(1 for r in accuracy_results if r.get('data_preserved'))
        
        print(f"\n📊 Accuracy Statistics (n={len(accuracy_results)}):")
        print(f"    Count matches:    {count_matches}/{len(accuracy_results)} ({count_matches/len(accuracy_results)*100:.1f}%)")
        print(f"    Data preserved:   {data_preserved}/{len(accuracy_results)} ({data_preserved/len(accuracy_results)*100:.1f}%)")
    
    # Value Assessment
    print("\n" + "=" * 80)
    print("VALUE ASSESSMENT")
    print("=" * 80)
    
    print(f"\n🎯 What MCP Adds:")
    print(f"    ✅ Product type identification (enables multi-product UIs)")
    print(f"    ✅ Rich metadata (15+ fields vs basic vehicle data)")
    print(f"    ✅ Standardized response format (MCP envelope)")
    print(f"    ✅ Request tracing (debugging & monitoring)")
    print(f"    ✅ Multi-backend support (vehicles + e-commerce + more)")
    print(f"    ✅ Frontend abstraction (one UI for all product types)")
    
    print(f"\n💰 Trade-offs:")
    if direct_latencies and mcp_latencies:
        print(f"    Cost:    +{overhead_mean:.1f}ms average latency ({overhead_pct:+.1f}%)")
        print(f"    Benefit: Data enrichment + multi-product support + standardization")
        
        if overhead_pct < 15:
            print(f"    Verdict: ✅ EXCELLENT - Minimal overhead for significant value")
        elif overhead_pct < 30:
            print(f"    Verdict: ✅ GOOD - Acceptable overhead for features gained")
        elif overhead_pct < 50:
            print(f"    Verdict: ⚠️  ACCEPTABLE - Consider optimization if latency-critical")
        else:
            print(f"    Verdict: ❌ REVIEW - High overhead, investigate bottlenecks")
    
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    
    if direct_latencies and mcp_latencies:
        if overhead_pct < 30:
        print(f"\n✅ MCP adapter adds valuable features with acceptable overhead")
        print(f"   • Use for: Multi-product UIs, rich metadata needs")
        print(f"   • Skip for: Ultra-low latency requirements (< 100ms target)")
        elif overhead_pct >= 30:
        print(f"\n⚠️  Consider optimizations:")
        print(f"   • Add Redis caching")
        print(f"   • Optimize transformation logic")
        print(f"   • Use connection pooling")
        print(f"   • Profile slow operations")
    
    print("\n" + "=" * 80)


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("\n🚀 Starting Benchmark...")
    print("   Note: IDSS backend must be running on port 8000")
    print("   Note: This will take ~30-60 seconds to complete\n")
    
    try:
        asyncio.run(run_benchmark())
        print("\n✅ Benchmark complete!\n")
    except KeyboardInterrupt:
        print("\n\n⚠️  Benchmark interrupted by user\n")
    except Exception as e:
        print(f"\n\n❌ Benchmark failed: {e}\n")
        import traceback
        traceback.print_exc()
