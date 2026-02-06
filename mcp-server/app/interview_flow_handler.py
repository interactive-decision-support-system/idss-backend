"""
Interview Flow Handler - Reusable class for managing multi-turn conversations

This handler manages the IDSS interview system for AI agents like Thomas.
It handles:
- Session management
- Question/answer tracking
- Status detection (INVALID, OK)
- Multi-turn conversation flow
- Response parsing

Usage:
    handler = InterviewFlowHandler(base_url="http://localhost:8001")
    
    # Start conversation
    response = handler.start_conversation("gaming laptop")
    
    # Handle response
    if handler.is_question(response):
        question = handler.get_question(response)
        quick_replies = handler.get_quick_replies(response)
        # Present to user, get answer
        
        # Continue conversation
        response = handler.continue_conversation(user_answer)
    
    elif handler.is_results(response):
        products = handler.get_products(response)
        # Display products to user
"""

import requests
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum


class InterviewStatus(Enum):
    """Status of interview conversation."""
    QUESTION = "question"  # System asking a question
    RESULTS = "results"    # Final results received
    ERROR = "error"        # Error occurred
    PROCESSING = "processing"  # System processing


@dataclass
class InterviewQuestion:
    """Represents a question in the interview."""
    question_text: str
    quick_replies: List[str]
    session_id: str
    domain: str
    response_type: str
    
    def __str__(self):
        return f"Question: {self.question_text}\nOptions: {', '.join(self.quick_replies)}"


@dataclass
class InterviewResults:
    """Represents final results from interview."""
    products: List[Dict[str, Any]]
    total_count: int
    filters_applied: Dict[str, Any]
    session_id: Optional[str]
    latency_ms: float
    
    def __str__(self):
        return f"Found {self.total_count} products (showing {len(self.products)})"


class InterviewFlowHandler:
    """
    Manages multi-turn interview conversations with the MCP server.
    
    This class provides a clean interface for AI agents to:
    1. Start conversations
    2. Handle questions and answers
    3. Track session state
    4. Parse responses
    5. Retrieve final results
    """
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        """
        Initialize the interview flow handler.
        
        Args:
            base_url: Base URL of the MCP server
        """
        self.base_url = base_url.rstrip('/')
        self.session_id: Optional[str] = None
        self.conversation_history: List[Dict[str, Any]] = []
        self.current_domain: Optional[str] = None
        
    def start_conversation(
        self, 
        query: str, 
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Start a new conversation/search.
        
        Args:
            query: User's initial query
            filters: Optional initial filters
            limit: Max number of results to return
            
        Returns:
            MCP API response
        """
        url = f"{self.base_url}/api/search-products"
        
        payload = {
            "query": query,
            "filters": filters or {},
            "limit": limit
        }
        
        # Reset state for new conversation
        self.session_id = None
        self.conversation_history = []
        
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        
        # Track in history
        self.conversation_history.append({
            "type": "query",
            "content": query,
            "filters": filters,
            "response": result
        })
        
        # Extract session_id if present
        if self.is_question(result):
            question = self.get_question(result)
            self.session_id = question.session_id
            self.current_domain = question.domain
        
        return result
    
    def continue_conversation(
        self,
        answer: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Continue an existing conversation with an answer.
        
        Args:
            answer: User's answer to the previous question
            session_id: Optional session ID (uses stored if not provided)
            
        Returns:
            MCP API response
        """
        url = f"{self.base_url}/api/search-products"
        
        # Use provided session_id or stored one
        sid = session_id or self.session_id
        
        if not sid:
            raise ValueError("No session_id available. Start a conversation first.")
        
        payload = {
            "query": answer,
            "session_id": sid
        }
        
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        
        # Track in history
        self.conversation_history.append({
            "type": "answer",
            "content": answer,
            "session_id": sid,
            "response": result
        })
        
        # Update session_id if it changed
        if self.is_question(result):
            question = self.get_question(result)
            self.session_id = question.session_id
        
        return result
    
    def is_question(self, response: Dict[str, Any]) -> bool:
        """
        Check if response contains a question.
        
        Args:
            response: MCP API response
            
        Returns:
            True if response is a question
        """
        if response.get("status") != "INVALID":
            return False
        
        constraints = response.get("constraints", [])
        if not constraints:
            return False
        
        return any(
            c.get("code") == "FOLLOWUP_QUESTION_REQUIRED" 
            for c in constraints
        )
    
    def is_results(self, response: Dict[str, Any]) -> bool:
        """
        Check if response contains final results.
        
        Args:
            response: MCP API response
            
        Returns:
            True if response contains products
        """
        return response.get("status") == "OK"
    
    def is_error(self, response: Dict[str, Any]) -> bool:
        """
        Check if response is an error.
        
        Args:
            response: MCP API response
            
        Returns:
            True if response is an error
        """
        status = response.get("status", "")
        return status not in ["OK", "INVALID"]
    
    def get_status(self, response: Dict[str, Any]) -> InterviewStatus:
        """
        Get the status of the response.
        
        Args:
            response: MCP API response
            
        Returns:
            InterviewStatus enum
        """
        if self.is_results(response):
            return InterviewStatus.RESULTS
        elif self.is_question(response):
            return InterviewStatus.QUESTION
        elif self.is_error(response):
            return InterviewStatus.ERROR
        else:
            return InterviewStatus.PROCESSING
    
    def get_question(self, response: Dict[str, Any]) -> InterviewQuestion:
        """
        Extract question details from response.
        
        Args:
            response: MCP API response
            
        Returns:
            InterviewQuestion object
            
        Raises:
            ValueError: If response doesn't contain a question
        """
        if not self.is_question(response):
            raise ValueError("Response does not contain a question")
        
        constraints = response.get("constraints", [])
        question_constraint = next(
            (c for c in constraints if c.get("code") == "FOLLOWUP_QUESTION_REQUIRED"),
            None
        )
        
        if not question_constraint:
            raise ValueError("Could not find question in constraints")
        
        details = question_constraint.get("details", {})
        
        return InterviewQuestion(
            question_text=question_constraint.get("message", ""),
            quick_replies=details.get("quick_replies", []),
            session_id=details.get("session_id", ""),
            domain=details.get("domain", ""),
            response_type=details.get("response_type", "question")
        )
    
    def get_products(self, response: Dict[str, Any]) -> InterviewResults:
        """
        Extract products from results response.
        
        Args:
            response: MCP API response
            
        Returns:
            InterviewResults object
            
        Raises:
            ValueError: If response doesn't contain results
        """
        if not self.is_results(response):
            raise ValueError("Response does not contain results")
        
        data = response.get("data", {})
        trace = response.get("trace", {})
        
        return InterviewResults(
            products=data.get("products", []),
            total_count=data.get("total_count", 0),
            filters_applied=trace.get("metadata", {}).get("applied_filters", {}),
            session_id=self.session_id,
            latency_ms=trace.get("timings_ms", {}).get("total", 0.0)
        )
    
    def get_error_message(self, response: Dict[str, Any]) -> str:
        """
        Extract error message from response.
        
        Args:
            response: MCP API response
            
        Returns:
            Error message string
        """
        constraints = response.get("constraints", [])
        if constraints:
            return constraints[0].get("message", "Unknown error")
        
        return response.get("message", "Unknown error occurred")
    
    def get_conversation_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the conversation.
        
        Returns:
            Dictionary with conversation statistics
        """
        questions_asked = sum(
            1 for item in self.conversation_history 
            if item["type"] == "answer"
        )
        
        return {
            "total_turns": len(self.conversation_history),
            "questions_asked": questions_asked,
            "current_session_id": self.session_id,
            "current_domain": self.current_domain,
            "conversation_length": questions_asked + 1  # Initial query + answers
        }
    
    def reset(self):
        """Reset the handler state for a new conversation."""
        self.session_id = None
        self.conversation_history = []
        self.current_domain = None


# Example usage and helper functions

def example_simple_flow():
    """Example of a simple interview flow."""
    print("="*80)
    print("EXAMPLE: Simple Interview Flow")
    print("="*80)
    
    handler = InterviewFlowHandler()
    
    # Start conversation
    print("\n1. Starting conversation: 'laptop'")
    response = handler.start_conversation("laptop")
    
    # Check status
    status = handler.get_status(response)
    print(f"   Status: {status.value}")
    
    if status == InterviewStatus.QUESTION:
        question = handler.get_question(response)
        print(f"\n2. Question received:")
        print(f"   {question}")
        
        # Simulate user choosing first option
        user_answer = question.quick_replies[0]
        print(f"\n3. User answers: '{user_answer}'")
        
        # Continue conversation
        response = handler.continue_conversation(user_answer)
        status = handler.get_status(response)
        print(f"   Status: {status.value}")
        
        if status == InterviewStatus.RESULTS:
            results = handler.get_products(response)
            print(f"\n4. Results received:")
            print(f"   {results}")
            print(f"   Products: {[p['name'] for p in results.products[:3]]}")
    
    # Show summary
    summary = handler.get_conversation_summary()
    print(f"\n5. Conversation Summary:")
    print(f"   Turns: {summary['total_turns']}")
    print(f"   Questions: {summary['questions_asked']}")
    print(f"   Domain: {summary['current_domain']}")


def example_complete_flow():
    """Example of a complete multi-turn interview."""
    print("\n" + "="*80)
    print("EXAMPLE: Complete Multi-Turn Interview")
    print("="*80)
    
    handler = InterviewFlowHandler()
    
    # Start
    query = "I need a gaming laptop"
    print(f"\nUser Query: '{query}'")
    response = handler.start_conversation(query)
    
    turn = 1
    while handler.is_question(response):
        question = handler.get_question(response)
        print(f"\nTurn {turn}:")
        print(f"  Question: {question.question_text}")
        print(f"  Options: {question.quick_replies}")
        
        # Simulate user choosing middle option
        answer = question.quick_replies[len(question.quick_replies)//2]
        print(f"  User Answer: '{answer}'")
        
        response = handler.continue_conversation(answer)
        turn += 1
        
        # Safety limit
        if turn > 10:
            print("  (Safety limit reached)")
            break
    
    if handler.is_results(response):
        results = handler.get_products(response)
        print(f"\nFinal Results:")
        print(f"  Total: {results.total_count} products")
        print(f"  Latency: {results.latency_ms:.1f}ms")
        print(f"  Sample products:")
        for i, product in enumerate(results.products[:5], 1):
            print(f"    {i}. {product['name']} - ${product.get('price_cents', 0)/100:.2f}")


if __name__ == "__main__":
    # Run examples
    try:
        example_simple_flow()
        example_complete_flow()
    except Exception as e:
        print(f"\nError running examples: {e}")
        print("Make sure MCP server is running on http://localhost:8001")
