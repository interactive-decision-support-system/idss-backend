"""
Test script for gaming PC example: optimizing selection of components 
for a gaming PC under $2,000, focusing on compatibility and efficiency.

This demonstrates:
1. Complex query parsing ("gaming PC under $2000")
2. Domain detection (Electronics)
3. Neo4j KG compatibility checking
4. PostgreSQL product search
5. IDSS ranking algorithms
6. Component compatibility verification
"""

import asyncio
import httpx
import json
from typing import Dict, Any, List

# MCP Server URL
MCP_SERVER_URL = "http://localhost:8001"
IDSS_BACKEND_URL = "http://localhost:8000"


async def test_gaming_pc_search():
    """Test searching for gaming PC components under $2000."""
    
    print("=" * 80)
    print("GAMING PC EXAMPLE TEST")
    print("Query: 'optimizing the selection of components for a gaming PC under $2,000'")
    print("Focus: Compatibility and efficiency")
    print("=" * 80)
    
    # Test 1: Search via MCP protocol
    print("\n[TEST 1] MCP Protocol: /api/search-products")
    print("-" * 80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        mcp_request = {
            "query": "gaming PC under $2000",
            "filters": {
                "category": "Electronics",
                "product_type": "desktop_pc",
                "price_max_cents": 200000,  # $2000
                "gpu_vendor": "NVIDIA"  # Gaming typically requires NVIDIA
            },
            "limit": 10
        }
        
        print(f"Request: {json.dumps(mcp_request, indent=2)}")
        
        response = await client.post(
            f"{MCP_SERVER_URL}/api/search-products",
            json=mcp_request
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"\nResponse Status: {data.get('status')}")
            print(f"Total Products: {data.get('data', {}).get('total_count', 0)}")
            
            products = data.get('data', {}).get('products', [])
            print(f"\nProducts Found: {len(products)}")
            
            for i, product in enumerate(products[:5], 1):
                print(f"\n  Product {i}:")
                print(f"    ID: {product.get('product_id')}")
                print(f"    Name: {product.get('name')}")
                print(f"    Price: ${product.get('price_cents', 0) / 100:.2f}")
                print(f"    Brand: {product.get('brand')}")
                print(f"    Category: {product.get('category')}")
            
            # Show trace information
            trace = data.get('trace', {})
            print(f"\nTrace Information:")
            print(f"  Sources: {trace.get('sources', [])}")
            print(f"  Cache Hit: {trace.get('cache_hit', False)}")
            print(f"  Timings: {trace.get('timings_ms', {})}")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
    
    # Test 2: Search via UCP protocol
    print("\n\n[TEST 2] UCP Protocol: /ucp/search")
    print("-" * 80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        ucp_request = {
            "parameters": {
                "query": "gaming PC components under $2000",
                "filters": {
                    "category": "Electronics",
                    "price_max_cents": 200000
                },
                "limit": 10
            }
        }
        
        print(f"Request: {json.dumps(ucp_request, indent=2)}")
        
        response = await client.post(
            f"{MCP_SERVER_URL}/ucp/search",
            json=ucp_request
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"\nResponse Status: {data.get('status')}")
            print(f"Total Products: {data.get('total_count', 0)}")
            
            products = data.get('products', [])
            print(f"\nProducts Found: {len(products)}")
            
            for i, product in enumerate(products[:5], 1):
                print(f"\n  Product {i}:")
                print(f"    ID: {product.get('product_id')}")
                print(f"    Name: {product.get('name')}")
                print(f"    Price: ${product.get('price', {}).get('amount', 0) / 100:.2f}")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
    
    # Test 3: Get specific product details
    print("\n\n[TEST 3] Get Product Details: /api/get-product")
    print("-" * 80)
    
    if products:
        product_id = products[0].get('product_id')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            get_request = {
                "product_id": product_id
            }
            
            print(f"Request: {json.dumps(get_request, indent=2)}")
            
            response = await client.post(
                f"{MCP_SERVER_URL}/api/get-product",
                json=get_request
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"\nResponse Status: {data.get('status')}")
                
                product = data.get('data', {})
                if product:
                    print(f"\nProduct Details:")
                    print(f"  ID: {product.get('product_id')}")
                    print(f"  Name: {product.get('name')}")
                    print(f"  Description: {product.get('description', '')[:100]}...")
                    print(f"  Price: ${product.get('price_cents', 0) / 100:.2f}")
                    print(f"  Brand: {product.get('brand')}")
                    print(f"  Category: {product.get('category')}")
                    print(f"  Available Qty: {product.get('available_qty', 0)}")
                    
                    # Show trace
                    trace = data.get('trace', {})
                    print(f"\nTrace:")
                    print(f"  Sources: {trace.get('sources', [])}")
                    print(f"  Cache Hit: {trace.get('cache_hit', False)}")
                    print(f"  Timings: {trace.get('timings_ms', {})}")
            else:
                print(f"Error: {response.status_code}")
                print(response.text)
    
    # Test 4: Test IDSS interview flow for gaming PC
    print("\n\n[TEST 4] IDSS Interview Flow: /chat (via IDSS backend)")
    print("-" * 80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        chat_request = {
            "message": "I want to build a gaming PC under $2000",
            "k": 2  # Ask 2 questions before recommendations
        }
        
        print(f"Request: {json.dumps(chat_request, indent=2)}")
        
        response = await client.post(
            f"{IDSS_BACKEND_URL}/chat",
            json=chat_request
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"\nResponse Type: {data.get('response_type')}")
            print(f"Message: {data.get('message')}")
            
            if data.get('response_type') == 'question':
                print(f"Quick Replies: {data.get('quick_replies', [])}")
            
            if data.get('recommendations'):
                print(f"\nRecommendations: {len(data.get('recommendations', []))} rows")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_gaming_pc_search())
