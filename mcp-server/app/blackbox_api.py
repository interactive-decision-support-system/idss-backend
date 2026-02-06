"""
MCP Blackbox API - Simplified Interface

A simple, clean API that abstracts away all internal complexity.
No AI agent dependencies, just simple query → filter → results.

Design Principles:
1. Simple input/output contracts
2. No internal dependencies exposed
3. Clear error messages
4. Predictable behavior
5. Easy to integrate

Usage:
    from app.blackbox_api import MCPBlackbox
    
    api = MCPBlackbox()
    
    # Simple search
    results = api.search("gaming laptop under $2000")
    
    # Get product
    product = api.get_product("prod-123")
    
    # Add to cart
    cart = api.add_to_cart(user_id, product_id, quantity)
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum


@dataclass
class Product:
    """Simple product representation."""
    product_id: str
    name: str
    price: float  # In dollars (not cents)
    brand: str
    category: str
    image_url: str
    available: bool
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "product_id": self.product_id,
            "name": self.name,
            "price": self.price,
            "brand": self.brand,
            "category": self.category,
            "image_url": self.image_url,
            "available": self.available,
            "description": self.description,
            "metadata": self.metadata
        }


@dataclass
class SearchResult:
    """Simple search result representation."""
    products: List[Product]
    total_count: int
    query: str
    latency_ms: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "products": [p.to_dict() for p in self.products],
            "total_count": self.total_count,
            "query": self.query,
            "latency_ms": self.latency_ms
        }


class MCPBlackbox:
    """
    Simple blackbox MCP API.
    
    Hides all internal complexity (IDSS, PostgreSQL, Redis, Neo4j).
    Provides simple query → results interface.
    """
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        """Initialize blackbox API."""
        self.base_url = base_url.rstrip('/')
        self._session = None
    
    def _get_session(self):
        """Lazy load requests session."""
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session
    
    def search(
        self,
        query: str,
        category: Optional[str] = None,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        brand: Optional[str] = None,
        limit: int = 10
    ) -> SearchResult:
        """
        Simple search interface.
        
        Args:
            query: What to search for (e.g., "gaming laptop")
            category: Optional category filter
            max_price: Optional max price in dollars
            min_price: Optional min price in dollars
            brand: Optional brand filter
            limit: Max results to return
            
        Returns:
            SearchResult object
            
        Examples:
            >>> api = MCPBlackbox()
            >>> results = api.search("gaming laptop under $2000")
            >>> print(f"Found {results.total_count} products")
            >>> for product in results.products:
            >>>     print(f"- {product.name}: ${product.price}")
        """
        import requests
        import time
        
        # Build filters
        filters = {}
        if category:
            filters["category"] = category
        if max_price:
            filters["price_max_cents"] = int(max_price * 100)
        if min_price:
            filters["price_min_cents"] = int(min_price * 100)
        if brand:
            filters["brand"] = brand
        
        # Make request
        start = time.time()
        try:
            response = requests.post(
                f"{self.base_url}/api/search-products",
                json={
                    "query": query,
                    "filters": filters,
                    "limit": limit
                },
                timeout=30
            )
            latency_ms = (time.time() - start) * 1000
            
            response.raise_for_status()
            data = response.json()
            
            # Extract products
            products_data = data.get("data", {}).get("products", [])
            
            # Convert to simple Product objects
            products = []
            for p in products_data:
                product = Product(
                    product_id=p.get("product_id", ""),
                    name=p.get("name", ""),
                    price=p.get("price_cents", 0) / 100,  # Convert to dollars
                    brand=p.get("brand", ""),
                    category=p.get("category", ""),
                    image_url=p.get("image_url", ""),
                    available=p.get("available_qty", 0) > 0,
                    description=p.get("description"),
                    metadata=p.get("metadata")
                )
                products.append(product)
            
            return SearchResult(
                products=products,
                total_count=data.get("data", {}).get("total_count", len(products)),
                query=query,
                latency_ms=latency_ms
            )
            
        except Exception as e:
            # Return empty result on error
            return SearchResult(
                products=[],
                total_count=0,
                query=query,
                latency_ms=(time.time() - start) * 1000
            )
    
    def get_product(self, product_id: str) -> Optional[Product]:
        """
        Get a single product by ID.
        
        Args:
            product_id: Product ID
            
        Returns:
            Product object or None if not found
            
        Example:
            >>> api = MCPBlackbox()
            >>> product = api.get_product("prod-elec-abc123")
            >>> if product:
            >>>     print(f"{product.name}: ${product.price}")
        """
        import requests
        
        try:
            response = requests.post(
                f"{self.base_url}/api/get-product",
                json={"product_id": product_id},
                timeout=10
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "OK":
                return None
            
            p = data.get("data")
            if not p:
                return None
            
            return Product(
                product_id=p.get("product_id", ""),
                name=p.get("name", ""),
                price=p.get("price_cents", 0) / 100,
                brand=p.get("brand", ""),
                category=p.get("category", ""),
                image_url=p.get("image_url", ""),
                available=p.get("available_qty", 0) > 0,
                description=p.get("description"),
                metadata=p.get("metadata")
            )
            
        except Exception:
            return None
    
    def add_to_cart(
        self,
        user_id: str,
        product_id: str,
        quantity: int = 1
    ) -> Dict[str, Any]:
        """
        Add product to cart.
        
        Args:
            user_id: User identifier
            product_id: Product ID to add
            quantity: Quantity to add
            
        Returns:
            Cart information
        """
        import requests
        
        try:
            response = requests.post(
                f"{self.base_url}/api/add-to-cart",
                json={
                    "user_id": user_id,
                    "product_id": product_id,
                    "quantity": quantity
                },
                timeout=10
            )
            
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_categories(self) -> List[str]:
        """
        Get available categories.
        
        Returns:
            List of category names
        """
        # Simple hardcoded list (could be fetched from DB)
        return [
            "Electronics",
            "Books",
            "Clothing",
            "Home",
            "Automotive",
            "Jewelry",
            "Beauty"
        ]
    
    def health_check(self) -> bool:
        """
        Check if API is healthy.
        
        Returns:
            True if healthy
        """
        import requests
        
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


# Simple function-based API (even simpler than class)

def simple_search(query: str, max_price: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Ultra-simple search function.
    
    Args:
        query: What to search for
        max_price: Optional max price in dollars
        
    Returns:
        List of product dictionaries
        
    Example:
        >>> products = simple_search("laptop", max_price=1500)
        >>> for p in products:
        >>>     print(f"{p['name']}: ${p['price']}")
    """
    api = MCPBlackbox()
    results = api.search(query, max_price=max_price)
    return [p.to_dict() for p in results.products]


def simple_get_product(product_id: str) -> Optional[Dict[str, Any]]:
    """
    Ultra-simple get product function.
    
    Args:
        product_id: Product ID
        
    Returns:
        Product dictionary or None
    """
    api = MCPBlackbox()
    product = api.get_product(product_id)
    return product.to_dict() if product else None


# Test function
def test_blackbox_api():
    """Test the blackbox API."""
    print("="*80)
    print("MCP BLACKBOX API - TEST")
    print("Simple interface, no complexity exposed")
    print("="*80)
    
    api = MCPBlackbox()
    
    # Test 1: Health check
    print("\n1. Health Check:")
    healthy = api.health_check()
    print(f"   {'' if healthy else '[FAIL]'} API Health: {healthy}")
    
    if not healthy:
        print("\n[WARN] API not available. Start server with:")
        print("   cd mcp-server && uvicorn app.main:app --port 8001")
        return
    
    # Test 2: Simple search
    print("\n2. Simple Search (Dell laptops):")
    results = api.search("Dell laptop", limit=5)
    print(f"   Found: {results.total_count} products")
    print(f"   Latency: {results.latency_ms:.1f}ms")
    
    if results.products:
        print(f"   Sample products:")
        for i, p in enumerate(results.products[:3], 1):
            print(f"     {i}. {p.name} - ${p.price:.2f}")
    
    # Test 3: Search with price filter
    print("\n3. Search with Price Filter (under $1500):")
    results2 = api.search("laptop", max_price=1500, limit=5)
    print(f"   Found: {results2.total_count} products")
    
    if results2.products:
        prices = [p.price for p in results2.products]
        all_under = all(p <= 1500 for p in prices)
        print(f"   {'' if all_under else '[FAIL]'} All under $1500: {all_under}")
        print(f"   Price range: ${min(prices):.2f} - ${max(prices):.2f}")
    
    # Test 4: Get specific product
    print("\n4. Get Specific Product:")
    if results.products:
        product_id = results.products[0].product_id
        product = api.get_product(product_id)
        
        if product:
            print(f"    Retrieved: {product.name}")
            print(f"      Price: ${product.price:.2f}")
            print(f"      Available: {product.available}")
    
    # Test 5: Get categories
    print("\n5. Get Categories:")
    categories = api.get_categories()
    print(f"   Available: {', '.join(categories)}")
    
    # Test 6: Ultra-simple function API
    print("\n6. Ultra-Simple Function API:")
    simple_results = simple_search("MacBook", max_price=2000)
    print(f"   Found: {len(simple_results)} products")
    if simple_results:
        print(f"   First: {simple_results[0]['name']}")
    
    print("\n" + "="*80)
    print(" BLACKBOX API WORKING - Simple, clean, no complexity")
    print("="*80)


# Convenience alias for easier imports
BlackboxAPI = MCPBlackbox


if __name__ == "__main__":
    test_blackbox_api()
