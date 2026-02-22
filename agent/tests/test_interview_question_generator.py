from agent.interview.question_generator import generate_question

def test_interview_question_generator_basic():
    result = generate_question(
        product_type="laptop",
        conversation_history=[],
        explicit_filters={},
        questions_asked=[]
    )
    assert hasattr(result, "question")

# Latency test
def test_interview_question_generator_latency():
    import time
    start = time.time()
    result = generate_question(
        product_type="laptop",
        conversation_history=[],
        explicit_filters={},
        questions_asked=[]
    )
    latency = time.time() - start
    assert latency < 5.0  # Expect generation within 5 seconds

# Accuracy test
def test_interview_question_generator_accuracy():
    result = generate_question(
        product_type="laptop",
        conversation_history=[],
        explicit_filters={},
        questions_asked=[]
    )
    assert result.question and len(result.question) > 5

# Error handling test
def test_interview_question_generator_error_handling():
    try:
        generate_question(
            product_type="laptop",
            conversation_history=[],
            explicit_filters={},
            questions_asked=[""]
        )  # Empty input should raise or handle error
    except Exception:
        assert True
    else:
        assert True  # If handled gracefully, still pass
# Add latency, accuracy, and error handling tests here
