# Export for FastAPI endpoints
__all__ = [
    "SessionResponse", "ResetRequest", "ResetResponse",
    "get_session_state", "reset_session", "delete_session", "list_sessions",
    "get_session_manager", "InterviewSessionManager", "InterviewSessionState"
]
"""
Session state management for laptop/electronics/book interviews.

Per kg.txt: track intent at 2 levels (big goal vs next move).
- Session intent: Explore | Decide today | Execute purchase
- Step intent: Research | Compare | Negotiate | Schedule | Return/post-purchase

Tracks conversation history, filters, questions asked.
Persists to Redis (mcp:session:{session_id}) per bigerrorjan29.txt.
Stores active_domain (vehicles|laptops|books|none), stage (INTERVIEW|RECOMMENDATIONS), question_index.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class SessionResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    filters: Dict[str, Any] = Field(default_factory=dict)
    questions_asked: List[str] = Field(default_factory=list)
    question_count: int = 0
    question_index: int = 0
    product_type: Optional[str] = None
    active_domain: Optional[str] = None
    stage: str = "INTERVIEW"
    session_intent: Optional[str] = None
    step_intent: Optional[str] = None
    conversation_length: int = 0

class ResetRequest(BaseModel):
    session_id: str = Field(..., description="Session ID to reset")

class ResetResponse(BaseModel):
    session_id: str = Field(..., description="Session ID that was reset")
    status: str = Field(..., description="Reset status")


import logging
logger = None
try:
    from app.utils.logger import get_logger
    logger = get_logger("interview.session_manager")
except ImportError:
    logger = logging.getLogger("interview.session_manager")

# Stage enum for session state
STAGE_INTERVIEW = "INTERVIEW"
STAGE_RECOMMENDATIONS = "RECOMMENDATIONS"
STAGE_CHECKOUT = "CHECKOUT"

# Session intent (kg.txt): overall mode for this shopping session
SESSION_INTENT_EXPLORE = "Explore"
SESSION_INTENT_DECIDE = "Decide today"
SESSION_INTENT_EXECUTE = "Execute purchase"

# Step intent (kg.txt): next action user wants right now
STEP_INTENT_RESEARCH = "Research"
STEP_INTENT_COMPARE = "Compare"
STEP_INTENT_NEGOTIATE = "Negotiate"
STEP_INTENT_SCHEDULE = "Schedule"
STEP_INTENT_RETURN = "Return/post-purchase"


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
    # User action tracking for refinement
    favorite_product_ids: List[str] = field(default_factory=list)  # Hearts/likes
    clicked_product_ids: List[str] = field(default_factory=list)  # View details clicks
    last_recommendation_ids: List[str] = field(default_factory=list)  # For Research/Compare (product IDs from last recs)
    last_recommendation_data: List[Dict[str, Any]] = field(default_factory=list)  # Full product dicts for comparison (no DB re-fetch needed)
    # kg.txt: intent at 2 levels
    session_intent: Optional[str] = None  # Explore | Decide today | Execute purchase
    step_intent: Optional[str] = None  # Research | Compare | Negotiate | Schedule | Return
    # UniversalAgent state (persisted across HTTP requests)
    agent_filters: Dict[str, Any] = field(default_factory=dict)  # Slot values gathered by agent
    agent_questions_asked: List[str] = field(default_factory=list)  # Slot names already asked
    agent_history: List[Dict[str, str]] = field(default_factory=list)  # Conversation history for LLM context


class InterviewSessionManager:
    """
    Manages interview sessions for laptops/electronics.
    
    Similar to IDSSController but for e-commerce products.
    """
    
    def __init__(self):
        """Initialize session manager (in-memory + Redis persistence)."""
        self.sessions: Dict[str, InterviewSessionState] = {}
        self._agent_cache = None
        self._last_kg_persist: Dict[str, float] = {}  # session_id -> timestamp (throttle)
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
            "favorite_product_ids": getattr(state, "favorite_product_ids", []),
            "clicked_product_ids": getattr(state, "clicked_product_ids", []),
            "last_recommendation_ids": getattr(state, "last_recommendation_ids", []),
            "last_recommendation_data": getattr(state, "last_recommendation_data", []),
            "session_intent": getattr(state, "session_intent", None),
            "step_intent": getattr(state, "step_intent", None),
            "agent_filters": getattr(state, "agent_filters", {}),
            "agent_questions_asked": getattr(state, "agent_questions_asked", []),
            "agent_history": getattr(state, "agent_history", [])[-10:],
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
            favorite_product_ids=d.get("favorite_product_ids", []),
            clicked_product_ids=d.get("clicked_product_ids", []),
            last_recommendation_ids=d.get("last_recommendation_ids", []),
            last_recommendation_data=d.get("last_recommendation_data", []),
            session_intent=d.get("session_intent"),
            step_intent=d.get("step_intent"),
            agent_filters=d.get("agent_filters", {}),
            agent_questions_asked=d.get("agent_questions_asked", []),
            agent_history=d.get("agent_history", []),
        )

    def add_favorite(self, session_id: str, product_id: str) -> None:
        """Record that user favorited a product (for preference refinement)."""
        session = self.get_session(session_id)
        if product_id not in session.favorite_product_ids:
            session.favorite_product_ids.append(product_id)
        self._persist(session_id)

    def remove_favorite(self, session_id: str, product_id: str) -> None:
        """Record that user unfavorited a product."""
        session = self.get_session(session_id)
        session.favorite_product_ids = [p for p in session.favorite_product_ids if p != product_id]
        self._persist(session_id)

    def add_click(self, session_id: str, product_id: str) -> None:
        """Record that user clicked/viewed a product (for interest refinement)."""
        session = self.get_session(session_id)
        if product_id not in session.clicked_product_ids:
            session.clicked_product_ids.append(product_id)
        self._persist(session_id)

    def set_last_recommendations(self, session_id: str, product_ids: List[str]) -> None:
        """Store product IDs from last recommendations (for Research/Compare and exclude in 'Show more like these')."""
        session = self.get_session(session_id)
        session.last_recommendation_ids = list(product_ids)[:24]  # Up to 24 (accumulated across show-more rounds)
        self._persist(session_id)

    def set_last_recommendation_data(self, session_id: str, products: List[Dict[str, Any]]) -> None:
        """Store full product dicts from last recommendations — avoids DB re-fetch for comparison."""
        session = self.get_session(session_id)
        # Keep only the fields useful for comparison narrative (trim large description blobs)
        slim = []
        keep_keys = {
            "id", "product_id", "name", "brand", "price", "product_type",
            "processor", "ram", "storage", "storage_type", "screen_size",
            "gpu", "battery_life", "os", "weight", "rating", "rating_count",
            "refresh_rate_hz", "resolution", "color",
            "image_url", "primary_image_url", "image",  # For format_product UI rendering
            # vehicles
            "make", "model", "year", "mileage", "trim", "fuel_type", "drivetrain",
            # books
            "author", "genre", "pages",
        }
        for p in products[:12]:
            slim.append({k: v for k, v in p.items() if k in keep_keys and v is not None})
        session.last_recommendation_data = slim
        self._persist(session_id)

    def recall_session_memory(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Recall session memory from Neo4j KG (kg.txt: "next time we meet, we can discuss this further").
        Returns session_intent, step_intent, important_info or None if unavailable.
        """
        try:
            from app.neo4j_config import get_connection
            from app.knowledge_graph import KnowledgeGraphBuilder
            conn = get_connection()
            if not conn.verify_connectivity():
                return None
            builder = KnowledgeGraphBuilder(conn)
            return builder.get_session_memory(session_id)
        except Exception:
            return None

    def get_session(self, session_id: str) -> InterviewSessionState:
        """Get or create a session. Load from Redis if available. Hydrate from KG when returning user has no Redis data."""
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
        # New session (no Redis data): try to recall from KG for returning users (kg.txt)
        state = InterviewSessionState()
        kg_memory = self.recall_session_memory(session_id)
        if kg_memory and kg_memory.get("important_info"):
            info = kg_memory["important_info"]
            if isinstance(info, dict):
                if info.get("active_domain") and not state.active_domain:
                    state.active_domain = info.get("active_domain")
                if info.get("filters") and not state.explicit_filters:
                    state.explicit_filters = dict(info.get("filters", {}))
                if info.get("session_intent") and not state.session_intent:
                    state.session_intent = info.get("session_intent")
                if info.get("step_intent") and not state.step_intent:
                    state.step_intent = info.get("step_intent")
                if info.get("stage") and state.stage == STAGE_INTERVIEW:
                    state.stage = info.get("stage", state.stage)
                if logger:
                    logger.info(f"Hydrated session from KG memory: {session_id}")
        self.sessions[session_id] = state
        if logger:
            logger.info(f"Created new session: {session_id}")
        return state

    def _persist(self, session_id: str) -> None:
        """Persist session to Redis and optionally to Neo4j (kg.txt session memory)."""
        cache = self._get_agent_cache()
        if cache and session_id in self.sessions:
            cache.set_session_data(session_id, self._state_to_dict(self.sessions[session_id]))
        # Persist to Neo4j KG when available (MemOS-style: save after agent)
        self._persist_to_kg(session_id)

    def _persist_to_kg(self, session_id: str) -> None:
        """Persist session memory to Neo4j when available (kg.txt, MemOS/OpenClaw pattern). Throttled to once per 30s per session."""
        import time
        now = time.time()
        if now - self._last_kg_persist.get(session_id, 0) < 30:
            return
        try:
            from app.neo4j_config import get_connection
            from app.knowledge_graph import KnowledgeGraphBuilder
            conn = get_connection()
            if not conn.verify_connectivity():
                return
            builder = KnowledgeGraphBuilder(conn)
            state = self.sessions.get(session_id)
            if not state:
                return
            important = self.get_important_info_for_next_meeting(session_id)
            builder.create_session_memory(
                session_id=session_id,
                user_id=None,
                session_intent=getattr(state, "session_intent", None) or "Explore",
                step_intent=getattr(state, "step_intent", None) or "Research",
                important_info=important,
            )
            self._last_kg_persist[session_id] = now
        except Exception:
            pass  # Neo4j optional
    
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

    def set_session_intent(self, session_id: str, intent: str) -> None:
        """Set session intent (kg.txt): Explore | Decide today | Execute purchase."""
        session = self.get_session(session_id)
        session.session_intent = intent
        self._persist(session_id)

    def set_step_intent(self, session_id: str, intent: str) -> None:
        """Set step intent (kg.txt): Research | Compare | Negotiate | Schedule | Return."""
        session = self.get_session(session_id)
        session.step_intent = intent
        self._persist(session_id)

    def get_important_info_for_next_meeting(self, session_id: str) -> Dict[str, Any]:
        """Collect important info to track across session for next meeting (kg.txt)."""
        session = self.get_session(session_id)
        return {
            "active_domain": session.active_domain,
            "filters": session.explicit_filters,
            "session_intent": getattr(session, "session_intent", None),
            "step_intent": getattr(session, "step_intent", None),
            "favorite_product_ids": getattr(session, "favorite_product_ids", []),
            "stage": session.stage,
        }

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
            "session_intent": getattr(session, "session_intent", None),
            "step_intent": getattr(session, "step_intent", None),
            "conversation_length": len(session.conversation_history)
        }


# Global session manager instance
# Global session manager instance
_session_manager = InterviewSessionManager()


def get_session_manager() -> InterviewSessionManager:
    """Get the global session manager instance."""
    return _session_manager

# API-facing functions for FastAPI endpoints
def get_session_state(session_id: str) -> SessionResponse:
    mgr = get_session_manager()
    session = mgr.get_session(session_id)
    return SessionResponse(
        session_id=session_id,
        filters=session.explicit_filters,
        questions_asked=session.questions_asked,
        question_count=session.question_count,
        question_index=session.question_index,
        product_type=session.product_type,
        active_domain=session.active_domain,
        stage=session.stage,
        session_intent=getattr(session, "session_intent", None),
        step_intent=getattr(session, "step_intent", None),
        conversation_length=len(session.conversation_history),
    )

def reset_session(session_id: str) -> ResetResponse:
    mgr = get_session_manager()
    mgr.reset_session(session_id)
    return ResetResponse(session_id=session_id, status="reset")

def delete_session(session_id: str) -> dict:
    mgr = get_session_manager()
    mgr.reset_session(session_id)
    return {"session_id": session_id, "status": "deleted"}

def list_sessions() -> dict:
    mgr = get_session_manager()
    return {"sessions": list(mgr.sessions.keys())}
