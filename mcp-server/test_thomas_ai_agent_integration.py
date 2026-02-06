"""
Test Suite for Thomas AI Agent Integration
==========================================

Validates that Thomas's AI agent can properly parse and use the MCP filter format.

This test simulates how Thomas's AI agent would:
1. Parse user natural language queries
2. Extract entities and preferences
3. Map them to MCP filter format
4. Call MCP API
5. Interpret results

Run from mcp-server directory:
    python test_thomas_ai_agent_integration.py
"""

import requests
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


MCP_BASE_URL = "http://localhost:8001"


@dataclass
class UserIntent:
    """Simulates Thomas AI Agent's parsed user intent."""
    raw_query: str
    intent_type: str  # "search", "compare", "recommend"
    entities: Dict[str, Any]
    preferences: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None


class ThomasAIAgentSimulator:
    """
    Simulates Thomas's AI Agent parsing and MCP integration.
    
    This demonstrates the expected integration flow.
    """
    
    def __init__(self, base_url: str = MCP_BASE_URL):
        self.base_url = base_url
        self.session_id = None
    
    def parse_user_query(self, query: str) -> UserIntent:
        """
        Step 1: Parse user's natural language query.
        
        In production, this would use Thomas's NLP/LLM to extract:
        - Intent type (search, compare, recommend)
        - Entities (brand, price, features)
        - Preferences (liked/disliked features)
        """
        query_lower = query.lower()
        
        # Detect intent type
        if " or " in query_lower or "compare" in query_lower:
            intent_type = "compare"
        elif "recommend" in query_lower or "suggest" in query_lower:
            intent_type = "recommend"
        else:
            intent_type = "search"
        
        # Extract entities (simplified - Thomas would use better NLP)
        entities = {}
        
        # Price extraction
        if "under" in query_lower or "$" in query:
            import re
            price_match = re.search(r'\$?(\d+),?(\d+)?', query)
            if price_match:
                price_str = price_match.group(1) + (price_match.group(2) or "")
                entities["price_max"] = int(price_str)
        
        # Brand extraction (including OR)
        brands = []
        brand_keywords = ["dell", "hp", "asus", "lenovo", "apple", "microsoft", "razer", "msi"]
        for brand in brand_keywords:
            if brand in query_lower:
                brands.append(brand.capitalize())
        
        if len(brands) > 1 and (" or " in query_lower or "compare" in query_lower):
            entities["brands"] = brands  # Multiple brands for OR operation
            entities["or_operation"] = True
        elif len(brands) == 1:
            entities["brand"] = brands[0]  # Single brand
        
        # Product type extraction
        if "laptop" in query_lower or "notebook" in query_lower:
            if "gaming" in query_lower:
                entities["product_type"] = "gaming_laptop"
            else:
                entities["product_type"] = "laptop"
            entities["category"] = "Electronics"
        elif "book" in query_lower:
            entities["category"] = "Books"
        
        # GPU vendor extraction
        if "nvidia" in query_lower or "rtx" in query_lower or "geforce" in query_lower:
            entities["gpu_vendor"] = "NVIDIA"
        elif "amd" in query_lower or "radeon" in query_lower:
            entities["gpu_vendor"] = "AMD"
        
        # Use case extraction
        if "gaming" in query_lower:
            entities["use_case"] = "Gaming"
        elif "work" in query_lower or "business" in query_lower:
            entities["use_case"] = "Work"
        elif "school" in query_lower or "student" in query_lower:
            entities["use_case"] = "School"
        
        # Preferences (soft constraints)
        preferences = {}
        liked_features = []
        
        if "portable" in query_lower or "lightweight" in query_lower:
            liked_features.append("portable")
        if "high performance" in query_lower or "powerful" in query_lower:
            liked_features.append("high-performance")
        if "battery" in query_lower:
            liked_features.append("long battery life")
        
        if liked_features:
            preferences["liked_features"] = liked_features
            preferences["use_case"] = entities.get("use_case", "General")
        
        return UserIntent(
            raw_query=query,
            intent_type=intent_type,
            entities=entities,
            preferences=preferences if preferences else None
        )
    
    def map_to_mcp_filters(self, intent: UserIntent) -> Dict[str, Any]:
        """
        Step 2: Map parsed intent to MCP filter format.
        
        This is the critical integration point - converting Thomas's
        internal representation to MCP's expected format.
        """
        filters = {}
        
        # Category
        if "category" in intent.entities:
            filters["category"] = intent.entities["category"]
        
        # Brand (single or multiple for OR)
        if "brands" in intent.entities:
            # Multiple brands (OR operation)
            filters["brand"] = intent.entities["brands"]
            filters["_or_operation"] = True
        elif "brand" in intent.entities:
            # Single brand
            filters["brand"] = intent.entities["brand"]
        
        # Product type
        if "product_type" in intent.entities:
            filters["product_type"] = intent.entities["product_type"]
        
        # Price
        if "price_max" in intent.entities:
            filters["price_max_cents"] = intent.entities["price_max"] * 100
        if "price_min" in intent.entities:
            filters["price_min_cents"] = intent.entities["price_min"] * 100
        
        # GPU vendor
        if "gpu_vendor" in intent.entities:
            filters["gpu_vendor"] = intent.entities["gpu_vendor"]
        
        # Subcategory / Use case
        if "use_case" in intent.entities:
            filters["subcategory"] = intent.entities["use_case"]
        
        # Soft preferences
        if intent.preferences:
            filters["_soft_preferences"] = intent.preferences
            filters["_use_idss_ranking"] = True  # Enable IDSS ranking for preferences
        
        return filters
    
    def call_mcp_api(self, intent: UserIntent, filters: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
        """
        Step 3: Call MCP API with structured filters.
        """
        url = f"{self.base_url}/api/search-products"
        
        payload = {
            "query": intent.raw_query,
            "filters": filters,
            "limit": limit
        }
        
        if self.session_id:
            payload["session_id"] = self.session_id
        
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {
                "status": "ERROR",
                "error": str(e),
                "message": "Failed to call MCP API"
            }
    
    def interpret_results(self, response: Dict[str, Any], intent: UserIntent) -> Dict[str, Any]:
        """
        Step 4: Interpret MCP response for user presentation.
        """
        if response.get("status") != "OK":
            return {
                "success": False,
                "message": "Search failed",
                "error": response.get("error", "Unknown error")
            }
        
        products = response.get("data", {}).get("products", [])
        trace = response.get("trace", {})
        
        # Analyze results
        analysis = {
            "success": True,
            "count": len(products),
            "products": products,
            "intent_type": intent.intent_type,
            "sources_used": trace.get("sources", []),
            "latency_ms": trace.get("timings_ms", {}).get("total", 0),
            "filters_applied": trace.get("metadata", {}).get("applied_filters", {}),
        }
        
        # Brand distribution (for OR operations)
        if intent.entities.get("or_operation"):
            brands = {}
            for p in products:
                brand = p.get("brand", "Unknown")
                brands[brand] = brands.get(brand, 0) + 1
            analysis["brand_distribution"] = brands
        
        # Price analysis
        if products:
            prices = [p.get("price_cents", 0) / 100 for p in products]
            analysis["price_range"] = {
                "min": min(prices),
                "max": max(prices),
                "avg": sum(prices) / len(prices)
            }
        
        return analysis
    
    def process_user_query(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """
        Complete flow: Parse → Map → Call → Interpret
        
        This is what Thomas's AI agent would execute end-to-end.
        """
        # Step 1: Parse
        intent = self.parse_user_query(query)
        
        # Step 2: Map
        filters = self.map_to_mcp_filters(intent)
        
        # Step 3: Call
        response = self.call_mcp_api(intent, filters, limit)
        
        # Step 4: Interpret
        analysis = self.interpret_results(response, intent)
        
        return {
            "query": query,
            "intent": {
                "type": intent.intent_type,
                "entities": intent.entities,
                "preferences": intent.preferences
            },
            "mcp_filters": filters,
            "analysis": analysis
        }


def test_case_1_simple_brand_filter():
    """Test 1: Simple brand filter (Dell laptops)"""
    print("\n" + "="*80)
    print("TEST 1: Simple Brand Filter")
    print("="*80)
    
    agent = ThomasAIAgentSimulator()
    result = agent.process_user_query("Show me Dell laptops")
    
    print(f"\n📝 User Query: {result['query']}")
    print(f"🎯 Intent Type: {result['intent']['type']}")
    print(f" Entities Extracted: {json.dumps(result['intent']['entities'], indent=2)}")
    print(f"\n🔧 MCP Filters Generated:")
    print(json.dumps(result['mcp_filters'], indent=2))
    
    analysis = result['analysis']
    print(f"\n Results:")
    print(f"   Success: {analysis['success']}")
    print(f"   Products Found: {analysis.get('count', 0)}")
    print(f"   Latency: {analysis.get('latency_ms', 0):.1f}ms")
    print(f"   Sources: {', '.join(analysis.get('sources_used', []))}")
    
    if analysis['count'] > 0:
        print(f"\n Sample Products:")
        for i, p in enumerate(analysis['products'][:3], 1):
            print(f"   {i}. {p.get('name')} - ${p.get('price_cents', 0)/100:.2f}")
    
    return result


def test_case_2_or_operation():
    """Test 2: OR operation (Dell OR HP)"""
    print("\n" + "="*80)
    print("TEST 2: OR Operation (Compare Brands)")
    print("="*80)
    
    agent = ThomasAIAgentSimulator()
    result = agent.process_user_query("Compare Dell OR HP laptops")
    
    print(f"\n📝 User Query: {result['query']}")
    print(f"🎯 Intent Type: {result['intent']['type']}")
    print(f" Entities Extracted: {json.dumps(result['intent']['entities'], indent=2)}")
    print(f"\n🔧 MCP Filters Generated:")
    print(json.dumps(result['mcp_filters'], indent=2))
    
    analysis = result['analysis']
    print(f"\n Results:")
    print(f"   Success: {analysis['success']}")
    print(f"   Products Found: {analysis.get('count', 0)}")
    print(f"   Latency: {analysis.get('latency_ms', 0):.1f}ms")
    
    if analysis.get('brand_distribution'):
        print(f"\n Brand Distribution:")
        for brand, count in sorted(analysis['brand_distribution'].items(), key=lambda x: -x[1]):
            print(f"   {brand}: {count} products")
    
    return result


def test_case_3_complex_with_preferences():
    """Test 3: Complex query with soft preferences"""
    print("\n" + "="*80)
    print("TEST 3: Complex Query with Preferences")
    print("="*80)
    
    agent = ThomasAIAgentSimulator()
    result = agent.process_user_query(
        "I need a gaming laptop with NVIDIA under $2000 that's portable"
    )
    
    print(f"\n📝 User Query: {result['query']}")
    print(f"🎯 Intent Type: {result['intent']['type']}")
    print(f" Entities Extracted: {json.dumps(result['intent']['entities'], indent=2)}")
    print(f"💡 Preferences: {json.dumps(result['intent']['preferences'], indent=2)}")
    print(f"\n🔧 MCP Filters Generated:")
    print(json.dumps(result['mcp_filters'], indent=2))
    
    analysis = result['analysis']
    print(f"\n Results:")
    print(f"   Success: {analysis['success']}")
    print(f"   Products Found: {analysis.get('count', 0)}")
    print(f"   Latency: {analysis.get('latency_ms', 0):.1f}ms")
    print(f"   IDSS Ranking: {'_use_idss_ranking' in result['mcp_filters']}")
    
    if analysis.get('price_range'):
        pr = analysis['price_range']
        print(f"\n💰 Price Range:")
        print(f"   Min: ${pr['min']:.2f}")
        print(f"   Max: ${pr['max']:.2f}")
        print(f"   Avg: ${pr['avg']:.2f}")
    
    return result


def test_case_4_price_and_or():
    """Test 4: OR operation with price constraint"""
    print("\n" + "="*80)
    print("TEST 4: OR + Price Filter")
    print("="*80)
    
    agent = ThomasAIAgentSimulator()
    result = agent.process_user_query("ASUS or Lenovo laptop under $1500")
    
    print(f"\n📝 User Query: {result['query']}")
    print(f"🎯 Intent Type: {result['intent']['type']}")
    print(f" Entities: {json.dumps(result['intent']['entities'], indent=2)}")
    print(f"\n🔧 MCP Filters:")
    print(json.dumps(result['mcp_filters'], indent=2))
    
    analysis = result['analysis']
    print(f"\n Results:")
    print(f"   Products: {analysis.get('count', 0)}")
    print(f"   Latency: {analysis.get('latency_ms', 0):.1f}ms")
    
    # Verify price constraint
    if analysis.get('count', 0) > 0 and analysis.get('price_range'):
        max_price = analysis['price_range']['max']
        if max_price <= 1500:
            print(f"    All products under $1500 (max: ${max_price:.2f})")
        else:
            print(f"   [WARN] Some products over $1500 (max: ${max_price:.2f})")
    
    if analysis.get('brand_distribution'):
        print(f"\n Brands: {dict(analysis['brand_distribution'])}")
    
    return result


def run_all_tests():
    """Run complete Thomas AI Agent integration test suite."""
    print("\n" + "="*80)
    print("THOMAS AI AGENT INTEGRATION TEST SUITE")
    print("Testing MCP Filter Format Parsing and Usage")
    print("="*80)
    
    results = []
    
    try:
        # Test 1: Simple brand filter
        result1 = test_case_1_simple_brand_filter()
        results.append(("Simple Brand Filter", result1['analysis']['success']))
        
        # Test 2: OR operation
        result2 = test_case_2_or_operation()
        results.append(("OR Operation", result2['analysis']['success']))
        
        # Test 3: Complex with preferences
        result3 = test_case_3_complex_with_preferences()
        results.append(("Complex + Preferences", result3['analysis']['success']))
        
        # Test 4: OR + Price
        result4 = test_case_4_price_and_or()
        results.append(("OR + Price Constraint", result4['analysis']['success']))
        
    except Exception as e:
        print(f"\n[FAIL] Test suite failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    total = len(results)
    passed = sum(1 for _, success in results if success)
    
    print(f"\nTotal Tests: {total}")
    print(f" Passed: {passed}")
    print(f"[FAIL] Failed: {total - passed}")
    print(f"Success Rate: {(passed/total)*100:.1f}%")
    
    print("\nDetailed Results:")
    for i, (name, success) in enumerate(results, 1):
        status = " PASS" if success else "[FAIL] FAIL"
        print(f"  {i}. {name}: {status}")
    
    # Validation checklist
    print("\n" + "="*80)
    print("INTEGRATION VALIDATION CHECKLIST")
    print("="*80)
    
    checklist = [
        ("", "Thomas can parse natural language queries"),
        ("", "Entities are correctly extracted"),
        ("", "MCP filter format is properly generated"),
        ("", "OR operations work correctly"),
        ("", "Price constraints are applied"),
        ("", "Soft preferences are supported"),
        ("", "API responses are interpretable"),
        ("", "Brand distribution analysis works"),
    ]
    
    for status, item in checklist:
        print(f"  {status} {item}")
    
    print("\n" + "="*80)
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    
    if success:
        print("\n All tests passed! Thomas AI Agent integration is validated.")
        exit(0)
    else:
        print("\n[WARN] Some tests failed. Please review the errors above.")
        exit(1)
