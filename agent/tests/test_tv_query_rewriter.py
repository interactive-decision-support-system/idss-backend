from agent.query_rewriter import rewrite

def test_tv_vague_query_triggers_clarification():
    result = rewrite("best tv", session_history=[], domain="tvs", current_filters={}, question_count=0)
    assert result.is_clarification is True

def test_tv_specific_query_no_clarification():
    result = rewrite("55 inch oled tv under 1500", session_history=[], domain="tvs", current_filters={}, question_count=0)
    assert result.is_clarification is False

def test_tv_gaming_console_enrichment():
    result = rewrite("tv for ps5 gaming", session_history=[], domain="tvs", current_filters={}, question_count=0)
    assert "use_case: gaming" in result.rewritten or "use_case: gaming" in (result.clarifying_question or "")

def test_tv_living_room_enrichment():
    result = rewrite("tv for the living room", session_history=[], domain="tvs", current_filters={}, question_count=0)
    assert "55" in result.rewritten or "75" in result.rewritten  # should mention size guidance

def test_tv_sports_enrichment():
    result = rewrite("tv for watching super bowl", session_history=[], domain="tvs", current_filters={}, question_count=0)
    assert "use_case: sports" in result.rewritten
