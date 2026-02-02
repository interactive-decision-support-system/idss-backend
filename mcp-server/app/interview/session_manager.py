"""
Session state management for laptop/electronics/book interviews.

Tracks conversation history, filters, questions asked.
Persists to Redis (mcp:session:{session_id}) per bigerrorjan29.txt.
Stores active_domain (vehicles|laptops|books|none), stage (INTERVIEW|RECOMMENDATIONS), question_index.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json

logger = None
try:
    from app.utils.logger import get_logger
    logger = get_logger("interview.session_manager")
except ImportError:
    import logging
    logger = logging.getLogger("interview.session_manager")

# Stage enum for session state
STAGE_INTERVIEW = "INTERVIEW"
STAGE_RECOMMENDATIONS = "RECOMMENDATIONS"
STAGE_CHECKOUT = "CHECKOUT"


@dataclass
class InterviewSessionState:
    """State for an interview session (laptops/electronics/books)."""
    explicit_filters: Dict[str, Any] = field(default_factory=dict)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    questions_asked: List[str] = field(default_factory=list)
    question_count: int = 0
    product_type: Optional[str] = None  # "laptop", "electronics", "book"
    active_domain: Optional[str] = None  # "laptops", "books", "vehicles", "none"
    stage: str = STAGE_INTERVIEW  # INTERVIEW | RECOMMENDATIONS | CHECKOUT
    question_index: int = 0  # 0-based slot (Q1, Q2, Q3)


class InterviewSessionManager:
    """
    Manages interview sessions for laptops/electronics.
    
    Similar to IDSSController but for e-commerce products.
    """
    
    def __init__(self):
        """Initialize session manager (in-memory + Redis persistence)."""
        self.sessions: Dict[str, InterviewSessionState] = {}
        self._agent_cache = None
        if logger:
            logger.info("InterviewSessionManager initialized")

    def _get_agent_cache(self):
        """Lazy init agent cache (Redis) for session persistence."""
        if self._agent_cache is None:
            try:
                from app.cache import agent_cache_client
                self._agent_cache = agent_cache_client
            except Exception:
                self._agent_cache = False
        return self._agent_cache if self._agent_cache else None

    def _state_to_dict(self, state: InterviewSessionState) -> Dict[str, Any]:
        return {
            "explicit_filters": state.explicit_filters,
            "conversation_history": state.conversation_history[-10:],
            "questions_asked": state.questions_asked,
            "question_count": state.question_count,
            "product_type": state.product_type,
            "active_domain": state.active_domain,
            "stage": state.stage,
            "question_index": state.question_index,
        }

    def _dict_to_state(self, d: Dict[str, Any]) -> InterviewSessionState:
        return InterviewSessionState(
            explicit_filters=d.get("explicit_filters", {}),
            conversation_history=d.get("conversation_history", []),
            questions_asked=d.get("questions_asked", []),
            question_count=d.get("question_count", 0),
            product_type=d.get("product_type"),
            active_domain=d.get("active_domain"),
            stage=d.get("stage", STAGE_INTERVIEW),
            question_index=d.get("question_index", 0),
        )

    def get_session(self, session_id: str) -> InterviewSessionState:
        """Get or create a session. Load from Redis if available."""
        if session_id in self.sessions:
            return self.sessions[session_id]
        cache = self._get_agent_cache()
        if cache:
            data = cache.get_session_data(session_id)
            if data:
                state = self._dict_to_state(data)
                self.sessions[session_id] = state
                if logger:
                    logger.info(f"Loaded session from Redis: {session_id}")
                return state
        self.sessions[session_id] = InterviewSessionState()
        if logger:
            logger.info(f"Created new session: {session_id}")
        return self.sessions[session_id]

    def _persist(self, session_id: str) -> None:
        """Persist session to Redis."""
        cache = self._get_agent_cache()
        if cache and session_id in self.sessions:
            cache.set_session_data(session_id, self._state_to_dict(self.sessions[session_id]))
    
    def update_filters(self, session_id: str, filters: Dict[str, Any]) -> None:
        """Update filters for a session."""
        session = self.get_session(session_id)
        for key, value in filters.items():
            if value is not None and not key.startswith("_"):
                session.explicit_filters[key] = value
        self._persist(session_id)

    def set_active_domain(self, session_id: str, domain: str) -> None:
        """Set active_domain (vehicles|laptops|books|none)."""
        session = self.get_session(session_id)
        session.active_domain = domain
        self._persist(session_id)

    def set_stage(self, session_id: str, stage: str) -> None:
        """Set stage (INTERVIEW|RECOMMENDATIONS|CHECKOUT)."""
        session = self.get_session(session_id)
        session.stage = stage
        self._persist(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a message to conversation history."""
        session = self.get_session(session_id)
        session.conversation_history.append({
            "role": role,
            "content": content
        })
        if len(session.conversation_history) > 10:
            session.conversation_history = session.conversation_history[-10:]
        self._persist(session_id)

    def add_question_asked(self, session_id: str, topic: str) -> None:
        """Record that a question was asked about a topic."""
        session = self.get_session(session_id)
        if topic not in session.questions_asked:
            session.questions_asked.append(topic)
        session.question_count += 1
        session.question_index = min(session.question_count, 3)
        self._persist(session_id)

    def set_product_type(self, session_id: str, product_type: str) -> None:
        """Set the product type for this session."""
        session = self.get_session(session_id)
        session.product_type = product_type
        self._persist(session_id)

    def reset_session(self, session_id: str) -> None:
        """Reset a session (in-memory and Redis). Domain switch calls this."""
        if session_id in self.sessions:
            del self.sessions[session_id]
        cache = self._get_agent_cache()
        if cache:
            cache.delete_session_data(session_id)
        if logger:
            logger.info(f"Reset session: {session_id}")
    
    def should_ask_question(self, session_id: str, max_questions: int = 3) -> bool:
        """
        Determine if we should ask another question.
        
        Args:
            session_id: Session ID
            max_questions: Maximum number of questions to ask (default 3)
        
        Returns:
            True if we should ask another question
        """
        session = self.get_session(session_id)
        
        # Check if we've asked enough questions
        if session.question_count >= max_questions:
            if logger:
                logger.info(f"Session {session_id}: Hit question limit ({max_questions})")
            return False
        
        # Check if we have enough information
        filters = session.explicit_filters
        has_use_case = "use_case" in filters or "subcategory" in filters
        has_budget = "price_min_cents" in filters or "price_max_cents" in filters
        has_brand = "brand" in filters
        
        # If we have use_case, budget, and brand, we have enough
        if has_use_case and has_budget and has_brand:
            if logger:
                logger.info(f"Session {session_id}: Have enough information (use_case, budget, brand)")
            return False
        
        return True
    
    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Get a summary of the session state (for logging)."""
        session = self.get_session(session_id)
        return {
            "filters": session.explicit_filters,
            "questions_asked": session.questions_asked,
            "question_count": session.question_count,
            "question_index": session.question_index,
            "product_type": session.product_type,
            "active_domain": session.active_domain,
            "stage": session.stage,
            "conversation_length": len(session.conversation_history)
        }


# Global session manager instance
_session_manager = InterviewSessionManager()


def get_session_manager() -> InterviewSessionManager:
    """Get the global session manager instance."""
    return _session_manager
