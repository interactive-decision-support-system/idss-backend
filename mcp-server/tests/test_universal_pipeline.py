"""
Integration Test for Universal Agent Pipeline.

Simulates the chat flow to verify:
1. Domain detection
2. Priority-based questioning (High -> Medium -> Low)
3. Handoff to search
"""
import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))
# Add repo root for agent package
sys.path.append(os.path.join(os.path.dirname(__file__), "../../"))

from agent import ChatRequest, process_chat

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_test_scenario(scenario_name: str, domain: str, messages: list):
    """Run a conversation scenario."""
    print(f"\n=== Testing Scenario: {scenario_name} ({domain}) ===")
    session_id = f"test-{domain}-{os.getpid()}"
    
    for user_msg in messages:
        print(f"User: {user_msg}")
        request = ChatRequest(message=user_msg, session_id=session_id)
        response = await process_chat(request)
        
        print(f"Agent: {response.message}")
        if response.response_type == "recommendations":
            print(f"[SUCCESS] Recommendation reached! Items found: {len(response.recommendations) if response.recommendations else 0}")
            if response.recommendations and len(response.recommendations) > 0 and len(response.recommendations[0]) > 0:
                first_item = response.recommendations[0][0]
                print(f"Sample Item Type: {first_item.get('productType')}")
                print(f"Sample Item Name: {first_item.get('name')}")
                if 'vehicle' in first_item:
                    print(f"Vehicle Details: {first_item['vehicle']}")
                if 'laptop' in first_item:
                    print(f"Laptop Details: {first_item['laptop']}")
            return
        elif response.response_type == "question":
             print(f"[INFO] Asked question: {response.message}")
        
    print(f"[WARNING] Scenario ended without recommendations.")

async def main():
    # Test 1: Vehicles (High Priority Slots: Budget, Use Case)
    await run_test_scenario(
        "Vehicle Search", 
        "vehicles",
        [
            "I want to buy a car", 
            "My budget is under $20,000", # Should trigger next High Priority question (Use Case)
            "I need it for commuting"     # Should trigger Medium/Low or Search
        ]
    )

    # Test 2: Laptops
    await run_test_scenario(
        "Laptop Search",
        "laptops",
        [
            "I need a laptop",
            "I will use it for gaming",    # High Priority
            "Under $1500"                  # High Priority -> triggered search or Brand
        ]
    )

if __name__ == "__main__":
    asyncio.run(main())
