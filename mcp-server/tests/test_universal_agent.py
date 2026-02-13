"""
Unit tests for UniversalAgent (app/universal_agent.py).

Uses mocks for OpenAI so tests run without an API key.
Covers: domain detection, criteria extraction, slot priority, IDSS signals, handoff.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.universal_agent import (
    UniversalAgent,
    AgentState,
    SlotValue,
    ExtractedCriteria,
    DomainClassification,
    GeneratedQuestion,
    DEFAULT_MAX_QUESTIONS,
)
from app.domain_registry import get_domain_schema, SlotPriority, PreferenceSlot, DomainSchema


# ---------------------------------------------------------------------------
# Fixtures: mock OpenAI so agent can be constructed without API key
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_openai_client():
    """Patch OpenAI() to return a MagicMock so UniversalAgent can be created without API key."""
    mock_client = MagicMock()
    with patch("app.universal_agent.OpenAI", return_value=mock_client):
        yield mock_client


def _make_mini_laptop_schema():
    """Minimal laptop-like schema with 2 HIGH slots for testing priority."""
    return DomainSchema(
        domain="laptops",
        description="Laptops",
        slots=[
            PreferenceSlot(
                name="use_case",
                display_name="Primary Use",
                priority=SlotPriority.HIGH,
                description="Use case",
                example_question="What will you use it for?",
                example_replies=["Gaming", "Work"],
            ),
            PreferenceSlot(
                name="budget",
                display_name="Budget",
                priority=SlotPriority.HIGH,
                description="Budget",
                example_question="What is your budget?",
                example_replies=["Under $1000", "Over $1000"],
            ),
            PreferenceSlot(
                name="brand",
                display_name="Brand",
                priority=SlotPriority.MEDIUM,
                description="Brand",
                example_question="Preferred brand?",
                example_replies=["Dell", "HP"],
            ),
        ],
    )


@pytest.fixture
def mini_schema():
    return _make_mini_laptop_schema()


# ---------------------------------------------------------------------------
# Init and state
# ---------------------------------------------------------------------------

def test_agent_init():
    agent = UniversalAgent(session_id="s1")
    assert agent.session_id == "s1"
    assert agent.domain is None
    assert agent.filters == {}
    assert agent.state == AgentState.INTENT_DETECTION
    assert agent.question_count == 0
    assert agent.max_questions == DEFAULT_MAX_QUESTIONS
    assert agent.questions_asked == []


def test_agent_init_with_history():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello"}]
    agent = UniversalAgent(session_id="s2", history=history)
    assert agent.history == history


def test_agent_init_max_questions_override():
    agent = UniversalAgent(session_id="s3", max_questions=1)
    assert agent.max_questions == 1


# ---------------------------------------------------------------------------
# Domain detection (mocked)
# ---------------------------------------------------------------------------

def test_process_message_unknown_domain_returns_clarification():
    """When domain detection returns unknown, user gets Cars/Laptops/Books clarification."""
    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value=None):
        agent = UniversalAgent(session_id="test-unknown")
        out = agent.process_message("something ambiguous")
    assert out["response_type"] == "question"
    assert "Cars" in out["message"] or "Laptops" in out["message"] or "Books" in out["message"]
    assert out.get("quick_replies") is not None
    assert agent.domain is None  # Reset so we try again next time


def test_process_message_domain_detected_then_extraction_and_question():
    """Domain laptops -> extract criteria (no recommend) -> missing slot -> ask question."""
    criteria_result = ExtractedCriteria(
        criteria=[SlotValue(slot_name="use_case", value="Gaming")],
        reasoning="ok",
        is_impatient=False,
        wants_recommendations=False,
    )
    agent = UniversalAgent(session_id="test-laptops")

    def extract_that_merges(msg, schema):
        if criteria_result.criteria:
            agent.filters.update({c.slot_name: c.value for c in criteria_result.criteria})
        return criteria_result

    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value="laptops"):
        with patch.object(UniversalAgent, "_extract_criteria", side_effect=extract_that_merges):
            with patch.object(UniversalAgent, "_generate_question") as mock_gen:
                mock_gen.return_value = GeneratedQuestion(
                    question="What is your budget? Feel free to share brand.",
                    quick_replies=["Under $1000", "Over $1000"],
                    topic="budget",
                )
                out = agent.process_message("I need a laptop for gaming")
    assert out["response_type"] == "question"
    assert "budget" in out["message"].lower() or "Budget" in out["message"]
    assert out.get("quick_replies") is not None
    assert agent.domain == "laptops"
    assert agent.filters.get("use_case") == "Gaming"


def test_process_message_impatient_skips_to_handoff():
    """When extraction says is_impatient=True, we handoff to search without asking more."""
    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value="laptops"):
        with patch.object(UniversalAgent, "_extract_criteria") as mock_extract:
            mock_extract.return_value = ExtractedCriteria(
                criteria=[SlotValue(slot_name="use_case", value="Work")],
                reasoning="ok",
                is_impatient=True,
                wants_recommendations=False,
            )
            agent = UniversalAgent(session_id="test-impatient")
            out = agent.process_message("just show me options")
    assert out["response_type"] == "recommendations_ready"
    assert out.get("filters") is not None
    assert out.get("domain") == "laptops"


def test_process_message_wants_recommendations_skips_to_handoff():
    """When extraction says wants_recommendations=True, handoff to search."""
    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value="laptops"):
        with patch.object(UniversalAgent, "_extract_criteria") as mock_extract:
            mock_extract.return_value = ExtractedCriteria(
                criteria=[],
                reasoning="ok",
                is_impatient=False,
                wants_recommendations=True,
            )
            agent = UniversalAgent(session_id="test-wants-rec")
            out = agent.process_message("what do you recommend?")
    assert out["response_type"] == "recommendations_ready"


def test_process_message_question_limit_triggers_handoff():
    """After max_questions asked, next turn handoffs even without impatient/wants_rec."""
    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value="laptops"):
        with patch.object(UniversalAgent, "_extract_criteria") as mock_extract:
            mock_extract.return_value = ExtractedCriteria(
                criteria=[SlotValue(slot_name="brand", value="Dell")],
                reasoning="ok",
                is_impatient=False,
                wants_recommendations=False,
            )
            agent = UniversalAgent(session_id="test-limit", max_questions=1)
            agent.question_count = 1  # Already at limit
            out = agent.process_message("Dell")
    assert out["response_type"] == "recommendations_ready"


# ---------------------------------------------------------------------------
# _should_recommend
# ---------------------------------------------------------------------------

def test_should_recommend_impatient(mini_schema):
    agent = UniversalAgent(session_id="s1")
    result = ExtractedCriteria(criteria=[], reasoning="", is_impatient=True, wants_recommendations=False)
    assert agent._should_recommend(result, mini_schema) is True


def test_should_recommend_wants_recommendations(mini_schema):
    agent = UniversalAgent(session_id="s1")
    result = ExtractedCriteria(criteria=[], reasoning="", is_impatient=False, wants_recommendations=True)
    assert agent._should_recommend(result, mini_schema) is True


def test_should_recommend_question_limit(mini_schema):
    agent = UniversalAgent(session_id="s1", max_questions=2)
    agent.question_count = 2
    assert agent._should_recommend(None, mini_schema) is True


def test_should_recommend_no_signal_and_under_limit(mini_schema):
    agent = UniversalAgent(session_id="s1")
    result = ExtractedCriteria(criteria=[], reasoning="", is_impatient=False, wants_recommendations=False)
    assert agent._should_recommend(result, mini_schema) is False


# ---------------------------------------------------------------------------
# _get_next_missing_slot
# ---------------------------------------------------------------------------

def test_get_next_missing_slot_returns_first_high(mini_schema):
    agent = UniversalAgent(session_id="s1")
    slot = agent._get_next_missing_slot(mini_schema)
    assert slot is not None
    assert slot.name in ("use_case", "budget")
    assert slot.priority == SlotPriority.HIGH


def test_get_next_missing_slot_skips_filled(mini_schema):
    agent = UniversalAgent(session_id="s1")
    agent.filters["use_case"] = "Gaming"
    slot = agent._get_next_missing_slot(mini_schema)
    assert slot is not None
    assert slot.name == "budget"


def test_get_next_missing_slot_skips_already_asked(mini_schema):
    agent = UniversalAgent(session_id="s1")
    agent.questions_asked.append("use_case")
    slot = agent._get_next_missing_slot(mini_schema)
    assert slot is not None
    assert slot.name == "budget"


def test_get_next_missing_slot_returns_medium_when_high_filled(mini_schema):
    agent = UniversalAgent(session_id="s1")
    agent.filters["use_case"] = "Gaming"
    agent.filters["budget"] = "$1000"
    slot = agent._get_next_missing_slot(mini_schema)
    assert slot is not None
    assert slot.name == "brand"
    assert slot.priority == SlotPriority.MEDIUM


def test_get_next_missing_slot_returns_none_when_all_filled_or_asked(mini_schema):
    agent = UniversalAgent(session_id="s1")
    agent.filters["use_case"] = "Gaming"
    agent.filters["budget"] = "$1000"
    agent.filters["brand"] = "Dell"
    slot = agent._get_next_missing_slot(mini_schema)
    assert slot is None


# ---------------------------------------------------------------------------
# _handoff_to_search
# ---------------------------------------------------------------------------

def test_handoff_to_search_shape():
    schema = get_domain_schema("laptops")
    assert schema is not None
    agent = UniversalAgent(session_id="handoff-test")
    agent.domain = "laptops"
    agent.filters = {"use_case": "Gaming", "budget": "$1500"}
    agent.question_count = 2
    out = agent._handoff_to_search(schema)
    assert out["response_type"] == "recommendations_ready"
    assert out["session_id"] == "handoff-test"
    assert out["domain"] == "laptops"
    assert out["filters"] == {"use_case": "Gaming", "budget": "$1500"}
    assert out["question_count"] == 2
    assert "questions_asked" in out


# ---------------------------------------------------------------------------
# Unknown / error paths
# ---------------------------------------------------------------------------

def test_process_message_unknown_domain_string_returns_clarification():
    """Domain 'unknown' from LLM is treated like None -> clarification."""
    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value="unknown"):
        agent = UniversalAgent(session_id="test")
        out = agent.process_message("???")
    assert out["response_type"] == "question"
    assert agent.domain is None


def test_process_message_no_schema_returns_error(mock_openai_client):
    """If domain is set to something with no schema (e.g. typo), return error."""
    agent = UniversalAgent(session_id="test")
    agent.domain = "nonexistent_domain_xyz"
    with patch("app.universal_agent.get_domain_schema", return_value=None):
        out = agent.process_message("anything")
    assert out["response_type"] == "error"
    assert "Something" in out["message"] or "wrong" in out["message"].lower() or "try" in out["message"].lower()


# ---------------------------------------------------------------------------
# Criteria extraction merges into filters
# ---------------------------------------------------------------------------

def test_extract_criteria_merges_into_filters(mini_schema):
    """When _extract_criteria returns criteria, they are merged into agent.filters."""
    criteria_result = ExtractedCriteria(
        criteria=[
            SlotValue(slot_name="use_case", value="Gaming"),
            SlotValue(slot_name="budget", value="$1200"),
        ],
        reasoning="ok",
        is_impatient=False,
        wants_recommendations=False,
    )
    agent = UniversalAgent(session_id="merge-test")

    def extract_that_merges(msg, schema):
        if criteria_result.criteria:
            agent.filters.update({c.slot_name: c.value for c in criteria_result.criteria})
        return criteria_result

    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value="laptops"):
        with patch.object(UniversalAgent, "_extract_criteria", side_effect=extract_that_merges):
            with patch.object(UniversalAgent, "_generate_question") as mock_gen:
                mock_gen.return_value = GeneratedQuestion(
                    question="Preferred brand?",
                    quick_replies=["Dell", "HP"],
                    topic="brand",
                )
                agent.process_message("gaming laptop around $1200")
    assert agent.filters.get("use_case") == "Gaming"
    assert agent.filters.get("budget") == "$1200"


# ---------------------------------------------------------------------------
# History updated
# ---------------------------------------------------------------------------

def test_process_message_appends_to_history():
    with patch.object(UniversalAgent, "_detect_domain_from_message", return_value=None):
        agent = UniversalAgent(session_id="hist")
        initial_len = len(agent.history)
        agent.process_message("hello")
        assert len(agent.history) >= initial_len + 2  # user + assistant reply


# ---------------------------------------------------------------------------
# _get_invite_topics (IDSS style)
# ---------------------------------------------------------------------------

def test_get_invite_topics_high_has_other_high(mini_schema):
    agent = UniversalAgent(session_id="s1")
    use_case_slot = next(s for s in mini_schema.slots if s.name == "use_case")
    topics = agent._get_invite_topics(use_case_slot, mini_schema)
    # Other HIGH slot is budget
    assert "Budget" in topics or "budget" in str(topics).lower()


def test_get_invite_topics_last_high_invites_medium(mini_schema):
    agent = UniversalAgent(session_id="s1")
    agent.questions_asked.append("use_case")  # only budget left at HIGH
    budget_slot = next(s for s in mini_schema.slots if s.name == "budget")
    topics = agent._get_invite_topics(budget_slot, mini_schema)
    assert "Brand" in topics or "brand" in str(topics).lower()


# ---------------------------------------------------------------------------
# Fallback when _generate_question fails
# ---------------------------------------------------------------------------

def test_generate_question_fallback_returns_schema_question(mini_schema, mock_openai_client):
    """When LLM fails, _generate_question returns fallback with example_question."""
    agent = UniversalAgent(session_id="s1")
    agent.client.beta.chat.completions.parse.side_effect = Exception("API error")
    slot = mini_schema.slots[0]
    result = agent._generate_question(slot, mini_schema)
    assert result.question  # fallback uses slot.example_question + invitation
    assert result.topic == slot.name
    assert result.quick_replies == slot.example_replies
