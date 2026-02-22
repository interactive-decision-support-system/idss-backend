from agent.interview.question_generator import generate_question

def test_question_generator_basic():
    result = generate_question(
        product_type="laptop",
        conversation_history=[],
        explicit_filters={},
        questions_asked=[]
    )
    assert hasattr(result, "question")

# Add latency, accuracy, and error handling tests here
