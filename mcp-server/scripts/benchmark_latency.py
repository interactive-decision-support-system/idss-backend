"""
Benchmark latency for MCP API endpoints.

Measures:
- get_product latency (cache hit vs miss)
- search_products latency
- add_to_cart latency
- Protocol overhead (MCP vs UCP vs Tool)
"""

import asyncio
import httpx
import time
import statistics
from typing import List, Dict, Any

MCP_SERVER_URL = "http://localhost:8001"


async def benchmark_get_product(product_id: str, iterations: int = 10) -> Dict[str, Any]:
    """Benchmark get_product endpoint."""
    print(f"\nBenchmarking get_product (product_id: {product_id})")
    print("-" * 80)
    
    latencies = []
    cache_hits = 0
    cache_misses = 0
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # First request (cache miss)
        start = time.time()
        response = await client.post(
            f"{MCP_SERVER_URL}/api/get-product",
            json={"product_id": product_id}
        )
        first_latency = (time.time() - start) * 1000
        cache_misses += 1
        latencies.append(first_latency)
        
        if response.status_code != 200:
            print(f"ERROR: {response.status_code} - {response.text}")
            return {}
        
        # Subsequent requests (cache hits)
        for i in range(iterations - 1):
            start = time.time()
            response = await client.post(
                f"{MCP_SERVER_URL}/api/get-product",
                json={"product_id": product_id}
            )
            latency = (time.time() - start) * 1000
            latencies.append(latency)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("trace", {}).get("cache_hit"):
                    cache_hits += 1
                else:
                    cache_misses += 1
    
    results = {
        "endpoint": "get_product",
        "iterations": iterations,
        "latencies_ms": latencies,
        "avg_latency_ms": statistics.mean(latencies),
        "median_latency_ms": statistics.median(latencies),
        "min_latency_ms": min(latencies),
        "max_latency_ms": max(latencies),
        "p95_latency_ms": statistics.quantiles(latencies, n=20)[18] if len(latencies) > 1 else latencies[0],
        "p99_latency_ms": statistics.quantiles(latencies, n=100)[98] if len(latencies) > 1 else latencies[0],
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_hit_rate": cache_hits / (cache_hits + cache_misses) if (cache_hits + cache_misses) > 0 else 0
    }
    
    print(f"  Average: {results['avg_latency_ms']:.2f}ms")
    print(f"  Median: {results['median_latency_ms']:.2f}ms")
    print(f"  Min: {results['min_latency_ms']:.2f}ms")
    print(f"  Max: {results['max_latency_ms']:.2f}ms")
    print(f"  P95: {results['p95_latency_ms']:.2f}ms")
    print(f"  P99: {results['p99_latency_ms']:.2f}ms")
    print(f"  Cache Hit Rate: {results['cache_hit_rate']*100:.1f}%")
    
    return results


async def benchmark_search_products(query: str, filters: Dict[str, Any], iterations: int = 10) -> Dict[str, Any]:
    """Benchmark search_products endpoint."""
    print(f"\nBenchmarking search_products (query: '{query}')")
    print("-" * 80)
    
    latencies = []
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(iterations):
            start = time.time()
            response = await client.post(
                f"{MCP_SERVER_URL}/api/search-products",
                json={
                    "query": query,
                    "filters": filters,
                    "limit": 10
                }
            )
            latency = (time.time() - start) * 1000
            latencies.append(latency)
            
            if response.status_code != 200:
                print(f"ERROR: {response.status_code} - {response.text}")
                return {}
    
    results = {
        "endpoint": "search_products",
        "iterations": iterations,
        "latencies_ms": latencies,
        "avg_latency_ms": statistics.mean(latencies),
        "median_latency_ms": statistics.median(latencies),
        "min_latency_ms": min(latencies),
        "max_latency_ms": max(latencies),
        "p95_latency_ms": statistics.quantiles(latencies, n=20)[18] if len(latencies) > 1 else latencies[0],
        "p99_latency_ms": statistics.quantiles(latencies, n=100)[98] if len(latencies) > 1 else latencies[0]
    }
    
    print(f"  Average: {results['avg_latency_ms']:.2f}ms")
    print(f"  Median: {results['median_latency_ms']:.2f}ms")
    print(f"  Min: {results['min_latency_ms']:.2f}ms")
    print(f"  Max: {results['max_latency_ms']:.2f}ms")
    print(f"  P95: {results['p95_latency_ms']:.2f}ms")
    print(f"  P99: {results['p99_latency_ms']:.2f}ms")
    
    return results


async def benchmark_add_to_cart(product_id: str, iterations: int = 10) -> Dict[str, Any]:
    """Benchmark add_to_cart endpoint."""
    print(f"\nBenchmarking add_to_cart (product_id: {product_id})")
    print("-" * 80)
    
    latencies = []
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(iterations):
            cart_id = f"benchmark-cart-{i}"
            start = time.time()
            response = await client.post(
                f"{MCP_SERVER_URL}/api/add-to-cart",
                json={
                    "cart_id": cart_id,
                    "product_id": product_id,
                    "qty": 1
                }
            )
            latency = (time.time() - start) * 1000
            latencies.append(latency)
            
            if response.status_code != 200:
                print(f"ERROR: {response.status_code} - {response.text}")
                return {}
    
    results = {
        "endpoint": "add_to_cart",
        "iterations": iterations,
        "latencies_ms": latencies,
        "avg_latency_ms": statistics.mean(latencies),
        "median_latency_ms": statistics.median(latencies),
        "min_latency_ms": min(latencies),
        "max_latency_ms": max(latencies),
        "p95_latency_ms": statistics.quantiles(latencies, n=20)[18] if len(latencies) > 1 else latencies[0],
        "p99_latency_ms": statistics.quantiles(latencies, n=100)[98] if len(latencies) > 1 else latencies[0]
    }
    
    print(f"  Average: {results['avg_latency_ms']:.2f}ms")
    print(f"  Median: {results['median_latency_ms']:.2f}ms")
    print(f"  Min: {results['min_latency_ms']:.2f}ms")
    print(f"  Max: {results['max_latency_ms']:.2f}ms")
    print(f"  P95: {results['p95_latency_ms']:.2f}ms")
    print(f"  P99: {results['p99_latency_ms']:.2f}ms")
    
    return results


async def benchmark_protocol_overhead():
    """Compare protocol overhead (MCP vs UCP vs Tool)."""
    print(f"\nBenchmarking Protocol Overhead")
    print("-" * 80)
    
    product_id = "laptop-001"
    results = {}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # MCP Protocol
        latencies_mcp = []
        for _ in range(5):
            start = time.time()
            response = await client.post(
                f"{MCP_SERVER_URL}/api/get-product",
                json={"product_id": product_id}
            )
            latencies_mcp.append((time.time() - start) * 1000)
        results["mcp"] = statistics.mean(latencies_mcp)
        
        # UCP Protocol
        latencies_ucp = []
        for _ in range(5):
            start = time.time()
            response = await client.post(
                f"{MCP_SERVER_URL}/ucp/get-product",
                json={
                    "action": "get_product",
                    "parameters": {"product_id": product_id}
                }
            )
            latencies_ucp.append((time.time() - start) * 1000)
        results["ucp"] = statistics.mean(latencies_ucp)
        
        # Tool Protocol
        latencies_tool = []
        for _ in range(5):
            start = time.time()
            response = await client.post(
                f"{MCP_SERVER_URL}/tools/execute",
                json={
                    "tool_name": "get_product",
                    "parameters": {"product_id": product_id}
                }
            )
            latencies_tool.append((time.time() - start) * 1000)
        results["tool"] = statistics.mean(latencies_tool)
    
    print(f"  MCP Protocol: {results['mcp']:.2f}ms")
    print(f"  UCP Protocol: {results['ucp']:.2f}ms")
    print(f"  Tool Protocol: {results['tool']:.2f}ms")
    
    return results


async def main():
    """Run all benchmarks."""
    print("=" * 80)
    print("MCP API LATENCY BENCHMARK")
    print("=" * 80)
    
    all_results = {}
    
    # Benchmark get_product
    get_product_results = await benchmark_get_product("laptop-001", iterations=20)
    all_results["get_product"] = get_product_results
    
    # Benchmark search_products
    search_results = await benchmark_search_products(
        "gaming laptop",
        {"category": "Electronics", "product_type": "gaming_laptop"},
        iterations=10
    )
    all_results["search_products"] = search_results
    
    # Benchmark add_to_cart
    add_to_cart_results = await benchmark_add_to_cart("laptop-001", iterations=10)
    all_results["add_to_cart"] = add_to_cart_results
    
    # Benchmark protocol overhead
    protocol_results = await benchmark_protocol_overhead()
    all_results["protocol_overhead"] = protocol_results
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\nget_product:")
    print(f"  Average: {get_product_results.get('avg_latency_ms', 0):.2f}ms")
    print(f"  Cache Hit Rate: {get_product_results.get('cache_hit_rate', 0)*100:.1f}%")
    
    print(f"\nsearch_products:")
    print(f"  Average: {search_results.get('avg_latency_ms', 0):.2f}ms")
    
    print(f"\nadd_to_cart:")
    print(f"  Average: {add_to_cart_results.get('avg_latency_ms', 0):.2f}ms")
    
    print(f"\nProtocol Overhead:")
    print(f"  MCP: {protocol_results.get('mcp', 0):.2f}ms")
    print(f"  UCP: {protocol_results.get('ucp', 0):.2f}ms")
    print(f"  Tool: {protocol_results.get('tool', 0):.2f}ms")
    
    # Save results to file
    import json
    with open("latency_benchmark_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    
    print("\nResults saved to latency_benchmark_results.json")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
