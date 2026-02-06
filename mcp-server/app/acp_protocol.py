"""
ACP (Agent Communication Protocol) - OpenAI Function Calling Format

Compatible with OpenAI Assistants API and Function Calling.
Provides MCP functionality in OpenAI-compatible format.

Usage:
    from app.acp_protocol import get_acp_tools, execute_acp_function
    
    # Get tool definitions for OpenAI
    tools = get_acp_tools()
    
    # Execute function call from OpenAI
    result = execute_acp_function(function_name, arguments)
"""

from typing import Dict, Any, List
import json


def get_acp_tools() -> List[Dict[str, Any]]:
    """
    Get MCP tools in OpenAI Function Calling format.
    
    Returns:
        List of tool definitions compatible with OpenAI API
        
    Note: Follows the official OpenAI Function Calling specification with:
    - Flat structure (no nested 'function' key)
    - Strict mode enabled for reliable schema adherence
    - additionalProperties set to false for each object
    """
    return [
        {
            "type": "function",
            "name": "search_products",
            "description": "Search for products in the e-commerce catalog. Returns a list of products matching the query and filters.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (e.g., 'gaming laptop', 'Dell laptop under $2000')"
                    },
                    "category": {
                        "type": ["string", "null"],
                        "enum": ["Electronics", "Books", "Clothing", "Home", "Automotive", "Beauty", "Accessories", "Art", None],
                        "description": "Product category to filter by (optional)"
                    },
                    "max_price": {
                        "type": ["number", "null"],
                        "description": "Maximum price in dollars (e.g., 2000 for $2000)"
                    },
                    "min_price": {
                        "type": ["number", "null"],
                        "description": "Minimum price in dollars"
                    },
                    "brand": {
                        "type": ["string", "null"],
                        "description": "Brand to filter by (e.g., 'Dell', 'HP', 'Apple')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10
                    }
                },
                "required": ["query", "category", "max_price", "min_price", "brand", "limit"],
                "additionalProperties": False
            }
        },
        {
            "type": "function",
            "name": "get_product",
            "description": "Get detailed information about a specific product by its ID.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The unique product ID (e.g., 'prod-elec-abc123')"
                    }
                },
                "required": ["product_id"],
                "additionalProperties": False
            }
        },
        {
            "type": "function",
            "name": "add_to_cart",
            "description": "Add a product to the user's shopping cart.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product ID to add to cart"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Quantity to add",
                        "default": 1
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier for the cart"
                    }
                },
                "required": ["product_id", "quantity", "user_id"],
                "additionalProperties": False
            }
        }
    ]


def execute_acp_function(
    function_name: str,
    arguments: Dict[str, Any],
    base_url: str = "http://localhost:8001"
) -> Dict[str, Any]:
    """
    Execute an ACP function call.
    
    Args:
        function_name: Name of the function to call
        arguments: Function arguments
        base_url: MCP server base URL
        
    Returns:
        Function result in OpenAI-compatible format
    """
    import requests
    
    try:
        # Map function names to endpoints
        endpoint_map = {
            "search_products": "/api/search-products",
            "get_product": "/api/get-product",
            "add_to_cart": "/api/add-to-cart"
        }
        
        endpoint = endpoint_map.get(function_name)
        if not endpoint:
            return {
                "error": f"Unknown function: {function_name}",
                "available_functions": list(endpoint_map.keys())
            }
        
        # Convert arguments to MCP format
        if function_name == "search_products":
            payload = {
                "query": arguments.get("query", ""),
                "filters": {},
                "limit": arguments.get("limit", 10)
            }
            
            # Add optional filters
            if "category" in arguments:
                payload["filters"]["category"] = arguments["category"]
            if "max_price" in arguments:
                payload["filters"]["price_max_cents"] = int(arguments["max_price"] * 100)
            if "min_price" in arguments:
                payload["filters"]["price_min_cents"] = int(arguments["min_price"] * 100)
            if "brand" in arguments:
                payload["filters"]["brand"] = arguments["brand"]
        
        elif function_name == "get_product":
            payload = {
                "product_id": arguments.get("product_id")
            }
        
        elif function_name == "add_to_cart":
            payload = {
                "user_id": arguments.get("user_id"),
                "product_id": arguments.get("product_id"),
                "quantity": arguments.get("quantity", 1)
            }
        
        # Make request
        response = requests.post(
            f"{base_url}{endpoint}",
            json=payload,
            timeout=30
        )
        
        response.raise_for_status()
        result = response.json()
        
        # Format for OpenAI
        if result.get("status") == "OK":
            data = result.get("data", {})
            
            # Simplify response for OpenAI
            if function_name == "search_products":
                products = data.get("products", [])
                simplified = []
                for p in products[:10]:  # Limit to prevent token overflow
                    simplified.append({
                        "product_id": p.get("product_id"),
                        "name": p.get("name"),
                        "price": f"${p.get('price_cents', 0)/100:.2f}",
                        "brand": p.get("brand"),
                        "category": p.get("category")
                    })
                
                return {
                    "success": True,
                    "products": simplified,
                    "total_count": data.get("total_count", len(products))
                }
            
            elif function_name == "get_product":
                p = data
                return {
                    "success": True,
                    "product": {
                        "product_id": p.get("product_id"),
                        "name": p.get("name"),
                        "price": f"${p.get('price_cents', 0)/100:.2f}",
                        "brand": p.get("brand"),
                        "description": p.get("description"),
                        "available": p.get("available_qty", 0) > 0,
                        "image_url": p.get("image_url")
                    }
                }
            
            else:  # add_to_cart
                return {
                    "success": True,
                    "cart": data
                }
        
        else:
            # Handle interview questions
            constraints = result.get("constraints", [])
            if constraints and constraints[0].get("code") == "FOLLOWUP_QUESTION_REQUIRED":
                question = constraints[0].get("message")
                options = constraints[0].get("details", {}).get("quick_replies", [])
                
                return {
                    "success": False,
                    "needs_clarification": True,
                    "question": question,
                    "options": options,
                    "message": f"I need more information: {question}"
                }
            
            return {
                "success": False,
                "error": result.get("message", "Unknown error")
            }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def format_for_openai_response(function_result: Dict[str, Any]) -> str:
    """
    Format function result for OpenAI API response.
    
    Args:
        function_result: Result from execute_acp_function
        
    Returns:
        Formatted string for OpenAI function response
    """
    if function_result.get("success"):
        # Success - return clean JSON
        return json.dumps(function_result, indent=2)
    elif function_result.get("needs_clarification"):
        # Needs user input
        return f"I need clarification: {function_result['question']}\nOptions: {', '.join(function_result.get('options', []))}"
    else:
        # Error
        return f"Error: {function_result.get('error', 'Unknown error')}"


# Example OpenAI integration
def example_openai_integration():
    """
    Example of how to use ACP with OpenAI API.
    """
    print("\n" + "="*80)
    print("EXAMPLE: OpenAI Integration with ACP")
    print("="*80)
    
    # Step 1: Get tool definitions
    tools = get_acp_tools()
    print("\n1. Tool definitions for OpenAI:")
    print(f"   {len(tools)} functions available:")
    for tool in tools:
        print(f"   - {tool['name']}: {tool['description'][:60]}...")
    
    # Step 2: Simulate OpenAI function call
    print("\n2. Simulating OpenAI function call:")
    print("   User: 'Find me a Dell laptop under $1500'")
    print("   OpenAI calls: search_products")
    
    function_call = {
        "name": "search_products",
        "arguments": {
            "query": "Dell laptop",
            "brand": "Dell",
            "max_price": 1500,
            "limit": 5
        }
    }
    
    print(f"   Function: {function_call['name']}")
    print(f"   Arguments: {json.dumps(function_call['arguments'], indent=6)}")
    
    # Step 3: Execute function
    result = execute_acp_function(
        function_call["name"],
        function_call["arguments"]
    )
    
    print("\n3. Function result:")
    print(f"   Success: {result.get('success')}")
    
    if result.get("success"):
        products = result.get("products", [])
        print(f"   Products found: {len(products)}")
        for p in products[:3]:
            print(f"     - {p['name']}: {p['price']}")
    elif result.get("needs_clarification"):
        print(f"   Question: {result['question']}")
        print(f"   Options: {result.get('options', [])}")
    
    # Step 4: Format for OpenAI
    openai_response = format_for_openai_response(result)
    print("\n4. Formatted for OpenAI:")
    print(f"   {openai_response[:200]}...")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    example_openai_integration()
