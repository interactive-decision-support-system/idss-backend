"""
Main IDSS Controller.

Orchestrates the interview and recommendation flow with configurable k parameter.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from idss.utils.logger import get_logger
from idss.interview.preference_slots import get_slot_status


def convert_numpy_types(obj: Any) -> Any:
    """Recursively convert numpy types to native Python types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    return obj
from idss.core.config import IDSSConfig, get_config
from idss.data.vehicle_store import LocalVehicleStore
from idss.parsing.semantic_parser import (
    parse_user_input,
    merge_filters,
    merge_preferences,
    ParsedInput
)
from idss.interview.question_generator import (
    generate_question,
    generate_question_for_dimension,
    generate_recommendation_intro,
    QuestionResponse
)
from idss.interview.entropy_question_selector import (
    select_question_dimension,
    get_dimension_topic,
    get_dimension_context,
)
from idss.diversification.entropy import (
    select_diversification_dimension,
    compute_entropy_report
)
from idss.diversification.bucketing import diversify_with_entropy_bucketing
from idss.recommendation.embedding_similarity import rank_with_embedding_similarity
from idss.recommendation.coverage_risk import rank_with_coverage_risk
from idss.recommendation.progressive_relaxation import progressive_filter_relaxation

logger = get_logger("core.controller")


@dataclass
class SessionState:
    """State for an IDSS session."""
    explicit_filters: Dict[str, Any] = field(default_factory=dict)
    implicit_preferences: Dict[str, Any] = field(default_factory=dict)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    questions_asked: List[str] = field(default_factory=list)
    asked_dimensions: set = field(default_factory=set)  # Dimensions already asked about
    question_count: int = 0
    domain: str = "vehicles"  # Domain: vehicles, laptops, books


@dataclass
class IDSSResponse:
    """Response from the IDSS controller."""
    response_type: str  # 'question' or 'recommendations'
    message: str
    quick_replies: Optional[List[str]] = None
    recommendations: Optional[List[List[Dict[str, Any]]]] = None  # 2D grid
    bucket_labels: Optional[List[str]] = None
    diversification_dimension: Optional[str] = None
    filters_extracted: Optional[Dict[str, Any]] = None
    preferences_extracted: Optional[Dict[str, Any]] = None
    domain: Optional[str] = None  # Domain: vehicles, laptops, books


class IDSSController:
    """
    Main controller for the Simplified IDSS.

    Handles the interview → recommendation flow with:
    - Configurable k (number of questions)
    - LLM-based impatience detection
    - Entropy-based diversification
    """

    def __init__(self, config: Optional[IDSSConfig] = None):
        """
        Initialize the controller.

        Args:
            config: Configuration object. Uses default config if not provided.
        """
        self.config = config or get_config()
        self.state = SessionState()
        self.store = LocalVehicleStore(require_photos=True)

        logger.info(f"IDSS Controller initialized: k={self.config.k}, method={self.config.recommendation_method}")

    def process_input(self, user_message: str) -> IDSSResponse:
        """
        Process user input and return appropriate response.

        Args:
            user_message: The user's message

        Returns:
            IDSSResponse with either a question or recommendations
        """
        logger.info(f"Processing input: {user_message[:100]}...")
        
        # Detect domain from message (for domain-aware parsing/questions)
        from idss.parsing.semantic_parser import detect_domain_from_message
        domain = detect_domain_from_message(user_message)
        # Store domain in state for question generation
        if not hasattr(self.state, 'domain'):
            self.state.domain = domain
        elif domain != "vehicles":  # Update if detected (don't override vehicles with default)
            self.state.domain = domain

        # Step 1: Parse the user input (with domain awareness)
        parsed = parse_user_input(
            user_message=user_message,
            conversation_history=self.state.conversation_history,
            existing_filters=self.state.explicit_filters,
            question_count=self.state.question_count,
            domain=getattr(self.state, 'domain', 'vehicles')
        )

        # Step 2: Update state with extracted information
        self._update_state(user_message, parsed)

        # Step 3: Decide whether to ask a question or recommend
        should_recommend = self._should_recommend(parsed)

        if should_recommend:
            return self._generate_recommendations()
        else:
            return self._generate_question()

    def get_recommendations(self) -> IDSSResponse:
        """
        Force recommendation generation (bypass interview).

        Returns:
            IDSSResponse with recommendations
        """
        return self._generate_recommendations()

    def reset_session(self) -> None:
        """Reset the session state for a new conversation."""
        self.state = SessionState()
        logger.info("Session reset")

    def _update_state(self, user_message: str, parsed: ParsedInput) -> None:
        """Update session state with parsed information."""
        # Merge filters
        self.state.explicit_filters = merge_filters(
            self.state.explicit_filters,
            parsed.explicit_filters
        )

        # Merge preferences
        self.state.implicit_preferences = merge_preferences(
            self.state.implicit_preferences,
            parsed.implicit_preferences
        )

        # Add to conversation history
        self.state.conversation_history.append({
            "role": "user",
            "content": user_message
        })

    def _should_recommend(self, parsed: ParsedInput) -> bool:
        """
        Determine if we should generate recommendations now.

        Returns True if:
        - k=0 (direct recommendation mode)
        - We've asked k questions already
        - User is impatient or explicitly wants recommendations
        """
        # k=0 mode: always recommend immediately
        if self.config.k == 0:
            logger.info("k=0 mode: generating recommendations immediately")
            return True

        # Hit question limit
        if self.state.question_count >= self.config.k:
            logger.info(f"Hit question limit (k={self.config.k}): generating recommendations")
            return True

        # User impatience detected
        if parsed.is_impatient:
            logger.info("Impatience detected: generating recommendations")
            return True

        # User explicitly wants recommendations
        if parsed.wants_recommendations:
            logger.info("User requested recommendations: generating recommendations")
            return True

        return False

    def _generate_question(self) -> IDSSResponse:
        """
        Generate a clarifying question.
        
        Strategy:
        1. PHASE 1 (Foundational): If HIGH priority slots (Budget, Use Case, Body) are missing, 
           ask about them using the Slot-based generator.
        2. PHASE 2 (Refinement): If basics are covered, use Entropy to ask the most 
           mathematically valuable question to split the search space.
        3. PHASE 3 (Fallback): If Entropy finds no good questions, fall back to 
           Medium/Low priority slots.
        """
        
        # 1. Analyze current knowledge
        slot_status = get_slot_status(
            self.state.explicit_filters, 
            self.state.implicit_preferences
        )
        
        # Check for High Priority holes (Budget, Body Style, Usage)
        high_priority_missing = [
            s for s in slot_status["missing"] 
            if s["priority"] == "HIGH"
        ]
        
        question_response = None

        # --- PHASE 1: FOUNDATION ---
        if high_priority_missing:
            logger.info(f"Phase 1: Missing {len(high_priority_missing)} high-priority slots. Enforcing Slot-based question.")
            # Get domain from state (default to vehicles)
            domain = getattr(self.state, 'domain', 'vehicles')
            
            question_response = generate_question(
                conversation_history=self.state.conversation_history,
                explicit_filters=self.state.explicit_filters,
                implicit_preferences=self.state.implicit_preferences,
                questions_asked=self.state.questions_asked,
                domain=domain
            )

        # --- PHASE 2: ENTROPY REFINEMENT ---
        else:
            # Only use entropy if basics are done AND it's enabled
            if self.config.use_entropy_questions:
                logger.info("Phase 2: Foundations set. Attempting Entropy-based question.")
                question_response = self._generate_entropy_based_question()
            
            # --- PHASE 3: FALLBACK ---
            # If entropy failed (data too sparse/clean) or was disabled, 
            # go back to the standard generator to ask about Medium/Low slots
            if question_response is None:
                logger.info("Phase 3: Entropy yielded no question. Falling back to standard generator.")
                domain = getattr(self.state, 'domain', 'vehicles')
                question_response = generate_question(
                    conversation_history=self.state.conversation_history,
                    explicit_filters=self.state.explicit_filters,
                    implicit_preferences=self.state.implicit_preferences,
                    questions_asked=self.state.questions_asked,
                    domain=domain
                )

        # Update state (same as before)
        self.state.question_count += 1
        self.state.questions_asked.append(question_response.topic)
        self.state.conversation_history.append({
            "role": "assistant",
            "content": question_response.question
        })

        logger.info(f"Generated question #{self.state.question_count}: {question_response.question}")

        domain = getattr(self.state, 'domain', 'vehicles')
        return IDSSResponse(
            response_type="question",
            message=question_response.question,
            quick_replies=question_response.quick_replies,
            filters_extracted=self.state.explicit_filters,
            preferences_extracted=self.state.implicit_preferences,
            domain=domain
        )

    def _generate_entropy_based_question(self) -> Optional[QuestionResponse]:
        """
        Generate a question based on entropy analysis of candidate set.

        Returns:
            QuestionResponse if a high-entropy dimension found, None otherwise
        """
        # Get preliminary candidates for entropy calculation
        candidates = self._get_preliminary_candidates(limit=200)

        if not candidates or len(candidates) < 10:
            logger.info("Not enough candidates for entropy-based question selection")
            return None

        # Select dimension with highest entropy
        selected_dim = select_question_dimension(
            candidates=candidates,
            explicit_filters=self.state.explicit_filters,
            asked_dimensions=self.state.asked_dimensions,
            min_entropy_threshold=0.3,
        )

        if selected_dim is None:
            logger.info("No suitable dimension for entropy-based question")
            return None

        # Get context about the dimension for LLM
        dim_context = get_dimension_context(selected_dim, candidates)

        # Generate question using LLM with dimension focus
        question_response = generate_question_for_dimension(
            dimension=selected_dim,
            dimension_context=dim_context,
            conversation_history=self.state.conversation_history,
            explicit_filters=self.state.explicit_filters,
            implicit_preferences=self.state.implicit_preferences,
        )

        # Track that we asked about this dimension
        self.state.asked_dimensions.add(selected_dim)

        logger.info(f"Entropy-based question for dimension '{selected_dim}': {question_response.question}")

        return question_response

    def _get_preliminary_candidates(self, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Get preliminary candidates based on current filters for entropy calculation.

        Args:
            limit: Maximum number of candidates to retrieve

        Returns:
            List of vehicle dictionaries
        """
        # Clean filters for database query
        db_filters = {}
        for key, value in self.state.explicit_filters.items():
            if value is not None:
                db_filters[key] = value

        # Add default year range if no filters
        if not db_filters:
            db_filters['year'] = '2018-2025'

        try:
            candidates = self.store.search_listings(
                filters=db_filters,
                limit=limit,
                order_by="year DESC, price ASC",
            )
            return candidates
        except Exception as e:
            logger.error(f"Failed to get preliminary candidates: {e}")
            return []

    def _generate_recommendations(self) -> IDSSResponse:
        """Generate recommendations with ranking method + entropy-based diversification."""
        logger.info("Generating recommendations...")
        logger.info(f"Filters: {self.state.explicit_filters}")
        logger.info(f"Preferences: {self.state.implicit_preferences}")

        # Step 1: Get candidate vehicles from database (larger pool for ranking)
        candidates = self._get_candidates(limit=500)

        domain = getattr(self.state, 'domain', 'vehicles')
        if not candidates:
            return IDSSResponse(
                response_type="recommendations",
                message="I couldn't find any vehicles matching your criteria. Try broadening your search.",
                recommendations=[],
                bucket_labels=[],
                filters_extracted=self.state.explicit_filters,
                preferences_extracted=self.state.implicit_preferences,
                domain=domain
            )

        logger.info(f"Found {len(candidates)} candidate vehicles from SQL")

        # Step 2: Rank candidates using configured method
        ranked_candidates = self._rank_candidates(candidates)
        logger.info(f"Ranked to {len(ranked_candidates)} candidates using {self.config.recommendation_method}")

        # Step 3: Log entropy report for analysis
        entropy_report = compute_entropy_report(ranked_candidates)
        logger.info(f"Entropy report: {entropy_report}")

        # Step 4: Select diversification dimension (based on ranked candidates)
        div_dimension = select_diversification_dimension(
            candidates=ranked_candidates,
            explicit_filters=self.state.explicit_filters
        )

        # Step 5: Bucket vehicles using entropy-based diversification (or skip if ablation)
        if self.config.use_entropy_bucketing:
            buckets, bucket_labels, _ = diversify_with_entropy_bucketing(
                vehicles=ranked_candidates,
                dimension=div_dimension,
                n_rows=self.config.num_rows,
                n_per_row=self.config.n_vehicles_per_row
            )
        else:
            # Ablation: just take top N vehicles without diversification
            logger.info("Entropy bucketing disabled (ablation mode)")
            total_vehicles = self.config.num_rows * self.config.n_vehicles_per_row
            flat_vehicles = ranked_candidates[:total_vehicles]
            # Split into rows without diversification
            buckets = []
            bucket_labels = []
            for i in range(self.config.num_rows):
                start = i * self.config.n_vehicles_per_row
                end = start + self.config.n_vehicles_per_row
                row = flat_vehicles[start:end]
                if row:
                    buckets.append(row)
                    bucket_labels.append(f"Row {i+1}")
            div_dimension = None

        # Step 6: Generate introduction message
        intro_message = generate_recommendation_intro(
            explicit_filters=self.state.explicit_filters,
            implicit_preferences=self.state.implicit_preferences,
            diversification_dimension=div_dimension,
            bucket_labels=bucket_labels
        )

        # Add to conversation history
        self.state.conversation_history.append({
            "role": "assistant",
            "content": intro_message
        })

        # Convert numpy types to native Python types for JSON serialization
        buckets = convert_numpy_types(buckets)

        domain = getattr(self.state, 'domain', 'vehicles')
        return IDSSResponse(
            response_type="recommendations",
            message=intro_message,
            recommendations=buckets,
            bucket_labels=bucket_labels,
            diversification_dimension=div_dimension,
            filters_extracted=self.state.explicit_filters,
            preferences_extracted=self.state.implicit_preferences,
            domain=domain
        )

    def _get_candidates(self, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Get candidate vehicles from the database.

        If progressive relaxation is enabled (default), progressively relaxes filters
        from least to most important until results are found.

        Args:
            limit: Maximum number of candidates to retrieve

        Returns:
            List of vehicle dictionaries
        """
        # Clean filters for database query
        db_filters = {}
        for key, value in self.state.explicit_filters.items():
            if value is not None:
                db_filters[key] = value

        try:
            if self.config.use_progressive_relaxation:
                # Use progressive filter relaxation to find candidates
                candidates, relaxation_state = progressive_filter_relaxation(
                    store=self.store,
                    explicit_filters=db_filters,
                    limit=limit
                )

                # Store relaxation state for potential use in response generation
                self._last_relaxation_state = relaxation_state

                if relaxation_state.get("relaxed_filters"):
                    logger.info(f"Filters relaxed to find results: {relaxation_state['relaxed_filters']}")

                return candidates
            else:
                # Simple query without relaxation (ablation mode)
                logger.info("Progressive relaxation disabled (ablation mode)")
                if not db_filters:
                    db_filters['year'] = '2018-2025'
                candidates = self.store.search_listings(
                    filters=db_filters,
                    limit=limit,
                    order_by="price",
                    order_dir="ASC"
                )
                self._last_relaxation_state = {"all_criteria_met": True, "relaxed_filters": []}
                return candidates
        except Exception as e:
            logger.error(f"Failed to get candidates: {e}")
            return []

    def _rank_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Rank candidates using configured recommendation method.

        Flow:
        1. SQL Filter → Candidates (500+)
        2. Embedding Similarity or Coverage-Risk Ranking → Top-100 ranked by relevance
        3. Returns ranked candidates for entropy bucketing

        Args:
            candidates: Raw candidates from SQL query

        Returns:
            Ranked list of candidates
        """
        method = self.config.recommendation_method
        top_k = 100  # Rank down to top 100 for entropy bucketing

        if method == "embedding_similarity":
            # Embedding Similarity: Dense Vector + MMR
            use_mmr = self.config.use_mmr_diversification
            logger.info(f"Ranking with Embedding Similarity (Dense + MMR={use_mmr})...")
            ranked = rank_with_embedding_similarity(
                vehicles=candidates,
                explicit_filters=self.state.explicit_filters,
                implicit_preferences=self.state.implicit_preferences,
                top_k=top_k,
                lambda_param=self.config.embedding_similarity_lambda_param,
                use_mmr=use_mmr
            )
        elif method == "coverage_risk":
            # Coverage-Risk Optimization
            logger.info(f"Ranking with Coverage-Risk Optimization...")
            ranked = rank_with_coverage_risk(
                vehicles=candidates,
                explicit_filters=self.state.explicit_filters,
                implicit_preferences=self.state.implicit_preferences,
                top_k=top_k,
                lambda_risk=self.config.coverage_risk_lambda_risk,
                mode=self.config.coverage_risk_mode,
                tau=self.config.coverage_risk_tau,
                alpha=self.config.coverage_risk_alpha
            )
        else:
            # Fallback: no ranking, just use first top_k
            logger.warning(f"Unknown method '{method}', returning unranked candidates")
            ranked = candidates[:top_k]

        return ranked


def create_controller(k: Optional[int] = None, **kwargs) -> IDSSController:
    """
    Factory function to create a controller with custom settings.

    Args:
        k: Number of questions to ask (overrides config)
        **kwargs: Other config parameters to override

    Returns:
        Configured IDSSController
    """
    config = get_config()

    # Override with provided parameters
    if k is not None:
        config.k = k
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)

    return IDSSController(config)
