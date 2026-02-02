"""
Pydantic models for IDSS API requests and responses.
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    message: str = Field(description="User's message")
    session_id: Optional[str] = Field(default=None, description="Session ID (auto-generated if not provided)")

    # Per-request config overrides
    k: Optional[int] = Field(default=None, description="Number of interview questions (0 = skip interview)")
    method: Optional[str] = Field(default=None, description="Recommendation method: 'embedding_similarity' or 'coverage_risk'")
    n_rows: Optional[int] = Field(default=None, description="Number of result rows")
    n_per_row: Optional[int] = Field(default=None, description="Vehicles per row")


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    response_type: str = Field(description="'question' or 'recommendations'")
    message: str = Field(description="AI response message")
    session_id: str = Field(description="Session ID")

    # Question-specific fields
    quick_replies: Optional[List[str]] = Field(default=None, description="Quick reply options for questions")

    # Recommendation-specific fields
    recommendations: Optional[List[List[Dict[str, Any]]]] = Field(default=None, description="2D grid of vehicles [rows][vehicles]")
    bucket_labels: Optional[List[str]] = Field(default=None, description="Labels for each row/bucket")
    diversification_dimension: Optional[str] = Field(default=None, description="Dimension used for diversification")

    # State info
    filters: Dict[str, Any] = Field(default_factory=dict, description="Extracted explicit filters")
    preferences: Dict[str, Any] = Field(default_factory=dict, description="Extracted implicit preferences")
    question_count: int = Field(default=0, description="Number of questions asked so far")


class SessionResponse(BaseModel):
    """Response model for session state endpoint."""
    session_id: str
    filters: Dict[str, Any]
    preferences: Dict[str, Any]
    question_count: int
    conversation_history: List[Dict[str, str]]


class ResetRequest(BaseModel):
    """Request model for session reset."""
    session_id: Optional[str] = None


class ResetResponse(BaseModel):
    """Response model for session reset."""
    session_id: str
    status: str


class RecommendRequest(BaseModel):
    """Request model for direct recommendation (bypass interview)."""
    session_id: Optional[str] = Field(default=None, description="Session ID")
    filters: Dict[str, Any] = Field(default_factory=dict, description="Explicit filters (make, model, price, etc.)")
    preferences: Dict[str, Any] = Field(default_factory=dict, description="Implicit preferences (liked_features, disliked_features)")
    method: Optional[str] = Field(default=None, description="Recommendation method: 'embedding_similarity' or 'coverage_risk'")
    n_rows: int = Field(default=3, description="Number of rows in output")
    n_per_row: int = Field(default=3, description="Vehicles per row")


class RecommendResponse(BaseModel):
    """Response model for direct recommendation."""
    session_id: str
    recommendations: List[List[Dict[str, Any]]] = Field(description="2D grid of vehicles")
    bucket_labels: List[str] = Field(description="Labels for each bucket")
    diversification_dimension: str = Field(description="Dimension used for diversification")
    total_candidates: int = Field(description="Total candidates before diversification")
    method_used: str = Field(description="Recommendation method used")


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    service: str
    version: str
    config: Dict[str, Any]
