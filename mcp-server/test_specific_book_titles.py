"""
Test specific book titles on MCP server endpoints.

Tests:
1. Search for exact book titles (e.g., "Dune", "The Hobbit")
2. Search for book by author (e.g., "Stephen King")
3. Search for book by genre (e.g., "Mystery", "Sci-Fi")
4. Test chat endpoint with book queries
5. Verify no filtering issues

Run from mcp-server directory:
    python test_specific_book_titles.py
"""

import requests
import json
from typing import Dict, List, Any

# Configuration
MCP_BASE_URL = "http://localhost:8001"
IDSS_BASE_URL = "http://localhost:8000"

def test_search_book_title(title: str) -> Dict[str, Any]:
    """Test searching for a specific book title."""
    print(f"\n{'='*80}")
    print(f"TEST: Searching for book title: '{title}'")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/api/search-products"
    payload = {
        "query": title,
        "filters": {
            "category": "Books"
        },
        "limit": 10
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        products = data.get("products", [])
        print(f"\n Status: {response.status_code}")
        print(f"Found {len(products)} results")
        
        if products:
            print("\nTop Results:")
            for i, product in enumerate(products[:5], 1):
                name = product.get("name", "Unknown")
                author = product.get("brand", "Unknown Author")
                price = product.get("price", 0)
                genre = product.get("subcategory", "Unknown")
                print(f"  {i}. {name}")
                print(f"     Author: {author}, Genre: {genre}, Price: ${price:.2f}")
        else:
            print("\n[WARN] No results found")
        
        return {"status": "success", "count": len(products), "products": products}
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}

def test_search_by_author(author: str) -> Dict[str, Any]:
    """Test searching for books by author."""
    print(f"\n{'='*80}")
    print(f"TEST: Searching for books by author: '{author}'")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/api/search-products"
    payload = {
        "query": f"books by {author}",
        "filters": {
            "category": "Books",
            "brand": author
        },
        "limit": 10
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # MCP response format: {"status": "OK", "data": {"products": [...]}}
        products = data.get("data", {}).get("products", [])
        print(f"\n Status: {response.status_code}")
        print(f"Found {len(products)} books by {author}")
        
        if products:
            print("\nBooks Found:")
            for i, product in enumerate(products, 1):
                name = product.get("name", "Unknown")
                price = product.get("price", 0)
                print(f"  {i}. {name} - ${price:.2f}")
        else:
            print(f"\n[WARN] No books found by {author}")
        
        return {"status": "success", "count": len(products), "products": products}
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}

def test_search_by_genre(genre: str) -> Dict[str, Any]:
    """Test searching for books by genre."""
    print(f"\n{'='*80}")
    print(f"TEST: Searching for {genre} books")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/api/search-products"
    payload = {
        "query": f"{genre} books",
        "filters": {
            "category": "Books",
            "subcategory": genre
        },
        "limit": 10
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # MCP response format: {"status": "OK", "data": {"products": [...]}}
        products = data.get("data", {}).get("products", [])
        print(f"\n Status: {response.status_code}")
        print(f"Found {len(products)} {genre} books")
        
        if products:
            print(f"\n{genre} Books:")
            for i, product in enumerate(products[:8], 1):
                name = product.get("name", "Unknown")
                author = product.get("brand", "Unknown")
                price = product.get("price", 0)
                print(f"  {i}. {name} by {author} - ${price:.2f}")
        else:
            print(f"\n[WARN] No {genre} books found")
        
        return {"status": "success", "count": len(products), "products": products}
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}

def test_chat_endpoint_books(query: str) -> Dict[str, Any]:
    """Test chat endpoint with book query."""
    print(f"\n{'='*80}")
    print(f"TEST: Chat endpoint - '{query}'")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/chat"
    payload = {
        "message": query,
        "session_id": f"test-{hash(query)}",
        "k": 0  # Skip interview, go straight to recommendations
    }
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        response_type = data.get("response_type")
        message = data.get("message", "")
        recommendations = data.get("recommendations", [])
        domain = data.get("domain")
        
        print(f"\n Status: {response.status_code}")
        print(f"Response Type: {response_type}")
        print(f"Domain: {domain}")
        print(f"Message: {message[:100]}...")
        
        if recommendations:
            total_items = sum(len(row) for row in recommendations)
            print(f"Recommendations: {len(recommendations)} rows, {total_items} total items")
            
            if recommendations and recommendations[0]:
                print("\nSample Recommendations:")
                for i, item in enumerate(recommendations[0][:3], 1):
                    name = item.get("name", "Unknown")
                    price = item.get("price", 0)
                    print(f"  {i}. {name} - ${price:.2f}")
        
        return {"status": "success", "response_type": response_type, "domain": domain}
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}

def test_get_product_by_id() -> Dict[str, Any]:
    """Test getting a specific book by product ID."""
    print(f"\n{'='*80}")
    print(f"TEST: Get specific product by ID")
    print('='*80)
    
    # First, get a book ID from search
    search_url = f"{MCP_BASE_URL}/api/search-products"
    search_payload = {"query": "Dune", "filters": {"category": "Books"}, "limit": 1}
    
    try:
        search_response = requests.post(search_url, json=search_payload, timeout=10)
        search_data = search_response.json()
        # MCP response format: {"status": "OK", "data": {"products": [...]}}
        products = search_data.get("data", {}).get("products", [])
        
        if not products:
            print("[WARN] No products found to test get_product")
            return {"status": "skipped"}
        
        product_id = products[0].get("product_id")
        print(f"Testing with product_id: {product_id}")
        
        # Now get the specific product
        get_url = f"{MCP_BASE_URL}/api/get-product"
        get_payload = {"product_id": product_id}
        
        response = requests.post(get_url, json=get_payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        product = data.get("product", {})
        print(f"\n Status: {response.status_code}")
        print(f"Product Found:")
        print(f"  Name: {product.get('name')}")
        print(f"  Author: {product.get('brand')}")
        print(f"  Genre: {product.get('subcategory')}")
        print(f"  Price: ${product.get('price', 0):.2f}")
        print(f"  Description: {product.get('description', '')[:100]}...")
        
        return {"status": "success", "product": product}
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}

def run_all_tests():
    """Run all book title tests."""
    print("\n" + "="*80)
    print("BOOK TITLE TESTING SUITE")
    print("Testing specific book titles and searches")
    print("="*80)
    
    results = {}
    
    # Test 1: Specific book titles
    book_titles = [
        "Dune",
        "The Hobbit",
        "Project Hail Mary",
        "Atomic Habits",
        "The Silent Patient"
    ]
    
    for title in book_titles:
        result = test_search_book_title(title)
        results[f"title_{title}"] = result
    
    # Test 2: Search by authors
    authors = [
        "Stephen King",
        "Andy Weir",
        "Emily Henry"
    ]
    
    for author in authors:
        result = test_search_by_author(author)
        results[f"author_{author}"] = result
    
    # Test 3: Search by genres
    genres = [
        "Sci-Fi",
        "Mystery",
        "Romance",
        "Fantasy"
    ]
    
    for genre in genres:
        result = test_search_by_genre(genre)
        results[f"genre_{genre}"] = result
    
    # Test 4: Chat endpoint
    chat_queries = [
        "I want a sci-fi book",
        "Show me mystery novels",
        "I need a book by Stephen King"
    ]
    
    for query in chat_queries:
        result = test_chat_endpoint_books(query)
        results[f"chat_{query[:20]}"] = result
    
    # Test 5: Get product by ID
    result = test_get_product_by_id()
    results["get_product"] = result
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    total_tests = len(results)
    successful = sum(1 for r in results.values() if r.get("status") == "success")
    failed = sum(1 for r in results.values() if r.get("status") == "error")
    
    print(f"Total Tests: {total_tests}")
    print(f" Successful: {successful}")
    print(f"[FAIL] Failed: {failed}")
    print(f"Success Rate: {(successful/total_tests)*100:.1f}%")
    
    # Detailed failures
    if failed > 0:
        print("\nFailed Tests:")
        for name, result in results.items():
            if result.get("status") == "error":
                print(f"  - {name}: {result.get('error', 'Unknown error')}")
    
    print("="*80)
    
    return results

if __name__ == "__main__":
    run_all_tests()
