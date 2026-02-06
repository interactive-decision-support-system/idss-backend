#!/usr/bin/env python3
"""Test semantic validation and fuzzy matching."""

import requests
import json

BASE_URL = "http://localhost:8001"

def test_invalid_inputs():
    """Test that invalid inputs are rejected."""
    print("\n" + "="*80)
    print("TEST 1: Invalid Input Rejection")
    print("="*80)
    
    invalid_inputs = [
        ("hi", "Should ask for domain selection"),
        ("hello", "Should ask for domain selection"),
        ("asdf", "Should reject gibberish"),
        ("123", "Should reject numbers only"),
        ("!!!", "Should reject special chars only"),
        ("a", "Should reject single letter"),
        ("ok", "Should reject very short input"),
    ]
    
    passed = 0
    failed = 0
    
    for test_input, description in invalid_inputs:
        r = requests.post(f"{BASE_URL}/chat", json={
            "message": test_input,
            "n_rows": 3,
            "n_per_row": 3
        })
        
        data = r.json()
        message = data.get("message", "")
        
        # Check if it asks for clarification or domain selection
        is_greeting_response = "What are you looking for" in message
        is_error = "didn't understand" in message.lower()
        
        if is_greeting_response or is_error or "Vehicles" in str(data.get("quick_replies", [])):
            print(f"   '{test_input}': {description}")
            print(f"     Response: {message[:80]}...")
            passed += 1
        else:
            print(f"  [FAIL] '{test_input}': {description}")
            print(f"     Response: {message[:80]}...")
            failed += 1
    
    print(f"\n  Summary: {passed}/{len(invalid_inputs)} passed")
    return passed == len(invalid_inputs)


def test_fuzzy_matching():
    """Test fuzzy matching for misspellings."""
    print("\n" + "="*80)
    print("TEST 2: Fuzzy Matching (Misspellings)")
    print("="*80)
    
    test_cases = [
        ("booksss", "books", "Extra 's' characters"),
        ("bookss", "books", "Extra 's' character"),
        ("boks", "books", "Missing 'o'"),
        ("lapto", "laptop", "Missing 'p'"),
        ("computr", "laptop", "Missing 'e', should match laptop"),
        ("notbook", "laptop", "Typo in 'notebook'"),
    ]
    
    passed = 0
    failed = 0
    
    for test_input, expected_domain, description in test_cases:
        r = requests.post(f"{BASE_URL}/chat", json={
            "message": test_input,
            "n_rows": 3,
            "n_per_row": 3
        })
        
        data = r.json()
        domain = data.get("domain", "")
        message = data.get("message", "")
        
        # Check if it asks domain-specific question
        is_book_question = "genre" in message.lower()
        is_laptop_question = "laptop" in message.lower() or "use" in message.lower()
        
        if expected_domain == "books" and is_book_question:
            print(f"   '{test_input}' → books: {description}")
            passed += 1
        elif expected_domain == "laptop" and is_laptop_question:
            print(f"   '{test_input}' → laptop: {description}")
            passed += 1
        else:
            print(f"  [FAIL] '{test_input}' expected {expected_domain}: {description}")
            print(f"     Response: {message[:80]}...")
            failed += 1
    
    print(f"\n  Summary: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


def test_valid_context_responses():
    """Test that valid short responses work in context."""
    print("\n" + "="*80)
    print("TEST 3: Valid Context Responses")
    print("="*80)
    
    # Start a laptop flow
    r = requests.post(f"{BASE_URL}/chat", json={"message": "laptops"})
    session_id = r.json()["session_id"]
    print(f"  Started session: {session_id}")
    
    # Valid short responses in context
    test_inputs = [
        ("Gaming", "Valid use case"),
        ("Dell", "Valid brand"),
        ("$700-$1200", "Valid price range"),
    ]
    
    passed = 0
    failed = 0
    
    for test_input, description in test_inputs:
        r = requests.post(f"{BASE_URL}/chat", json={
            "message": test_input,
            "session_id": session_id,
            "n_rows": 3,
            "n_per_row": 3
        })
        
        data = r.json()
        message = data.get("message", "")
        
        # Should not be rejected
        is_error = "didn't understand" in message.lower()
        
        if not is_error:
            print(f"   '{test_input}': {description}")
            print(f"     Response: {message[:60]}...")
            passed += 1
        else:
            print(f"  [FAIL] '{test_input}': {description} was rejected!")
            print(f"     Response: {message}")
            failed += 1
    
    print(f"\n  Summary: {passed}/{len(test_inputs)} passed")
    return passed == len(test_inputs)


def test_semantic_synonyms():
    """Test semantic synonym matching."""
    print("\n" + "="*80)
    print("TEST 4: Semantic Synonyms")
    print("="*80)
    
    test_cases = [
        ("computer", "Should match laptops"),
        ("notebook", "Should match laptops"),
        ("novel", "Should match books"),
    ]
    
    passed = 0
    failed = 0
    
    for test_input, description in test_cases:
        r = requests.post(f"{BASE_URL}/chat", json={
            "message": test_input,
            "n_rows": 3,
            "n_per_row": 3
        })
        
        data = r.json()
        message = data.get("message", "")
        
        # Check if appropriate domain question was asked
        has_laptop_question = "laptop" in message.lower() or "use" in message.lower()
        has_book_question = "genre" in message.lower()
        
        if "computer" in test_input or "notebook" in test_input:
            if has_laptop_question:
                print(f"   '{test_input}': {description}")
                passed += 1
            else:
                print(f"  [FAIL] '{test_input}': {description}")
                print(f"     Response: {message[:80]}...")
                failed += 1
        elif "novel" in test_input:
            if has_book_question:
                print(f"   '{test_input}': {description}")
                passed += 1
            else:
                print(f"  [FAIL] '{test_input}': {description}")
                print(f"     Response: {message[:80]}...")
                failed += 1
    
    print(f"\n  Summary: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


if __name__ == "__main__":
    print("\n" + "#"*80)
    print("# SEMANTIC VALIDATION & FUZZY MATCHING TESTS")
    print("#"*80)
    
    test1_passed = test_invalid_inputs()
    test2_passed = test_fuzzy_matching()
    test3_passed = test_valid_context_responses()
    test4_passed = test_semantic_synonyms()
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Test 1 (Invalid Input Rejection): {' PASSED' if test1_passed else '[FAIL] FAILED'}")
    print(f"Test 2 (Fuzzy Matching): {' PASSED' if test2_passed else '[FAIL] FAILED'}")
    print(f"Test 3 (Valid Context Responses): {' PASSED' if test3_passed else '[FAIL] FAILED'}")
    print(f"Test 4 (Semantic Synonyms): {' PASSED' if test4_passed else '[FAIL] FAILED'}")
    print("="*80 + "\n")
    
    if test1_passed and test2_passed and test3_passed and test4_passed:
        print(" All tests passed!")
        exit(0)
    else:
        print("[WARN]  Some tests failed!")
        exit(1)
