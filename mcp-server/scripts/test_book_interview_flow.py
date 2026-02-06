#!/usr/bin/env python3
"""
Test Book Interview Flow - Verify No More Loops

Tests that the book interview progresses through:
1. Genre question
2. Format question (NEW!)
3. Budget question (NEW!)
4. Show recommendations

Run: python scripts/test_book_interview_flow.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.query_specificity import is_specific_query, should_ask_followup, generate_followup_question


def test_genre_extraction():
    """Test that genre is extracted from user responses."""
    print("="*80)
    print("TEST 1: Genre Extraction")
    print("="*80)
    
    test_cases = [
        ("Sci-Fi", "Sci-Fi"),
        ("science fiction", "Science Fiction"),
        ("mystery", "Mystery"),
        ("fiction", "Fiction"),
        ("non-fiction", "Non-Fiction"),
        ("self-help", "Self-Help"),
        ("fantasy", "Fantasy"),
        ("thriller", "Thriller"),
    ]
    
    filters = {"category": "Books"}
    
    all_pass = True
    for user_input, expected_genre in test_cases:
        is_specific, extracted = is_specific_query(user_input, filters)
        actual_genre = extracted.get("genre")
        
        if actual_genre == expected_genre:
            print(f"   '{user_input}' → extracted genre: '{actual_genre}'")
        else:
            print(f"  [FAIL] '{user_input}' → expected '{expected_genre}', got '{actual_genre}'")
            all_pass = False
    
    return all_pass


def test_format_extraction():
    """Test that format is extracted from user responses."""
    print("\n" + "="*80)
    print("TEST 2: Format Extraction")
    print("="*80)
    
    test_cases = [
        ("hardcover", "Hardcover"),
        ("paperback", "Paperback"),
        ("ebook", "E-book"),
        ("kindle", "E-book"),
        ("audiobook", "Audiobook"),
        ("audible", "Audiobook"),
    ]
    
    filters = {"category": "Books"}
    
    all_pass = True
    for user_input, expected_format in test_cases:
        is_specific, extracted = is_specific_query(user_input, filters)
        actual_format = extracted.get("format")
        
        if actual_format == expected_format:
            print(f"   '{user_input}' → extracted format: '{actual_format}'")
        else:
            print(f"  [FAIL] '{user_input}' → expected '{expected_format}', got '{actual_format}'")
            all_pass = False
    
    return all_pass


def test_interview_progression():
    """Test that interview progresses through all questions."""
    print("\n" + "="*80)
    print("TEST 3: Interview Progression (No Loops)")
    print("="*80)
    
    # Simulate the interview flow
    filters = {"category": "Books", "product_type": "book"}
    
    # Turn 1: Initial query
    print("\n  Turn 1: User says 'Books'")
    should_ask, missing = should_ask_followup("books", filters)
    print(f"    Should ask followup? {should_ask}")
    print(f"    Missing info: {missing}")
    
    if should_ask and missing:
        question, replies = generate_followup_question("book", missing, filters)
        print(f"    Question: {question}")
        print(f"    Replies: {replies}")
        
        expected_first = "genre"
        if missing[0] == expected_first:
            print(f"     First question is about {expected_first}")
        else:
            print(f"    [FAIL] Expected {expected_first}, got {missing[0]}")
            return False
    
    # Turn 2: User responds with genre
    print("\n  Turn 2: User says 'Sci-Fi'")
    filters_updated = filters.copy()
    is_specific, extracted = is_specific_query("Sci-Fi", filters_updated)
    
    # Apply extracted genre
    if extracted.get("genre"):
        filters_updated["genre"] = extracted["genre"]
        filters_updated["subcategory"] = extracted["genre"]
    
    print(f"    Extracted: {extracted}")
    print(f"    Updated filters: genre={filters_updated.get('genre')}")
    
    should_ask, missing = should_ask_followup("Sci-Fi", filters_updated)
    print(f"    Should ask followup? {should_ask}")
    print(f"    Missing info: {missing}")
    
    if should_ask and missing:
        question, replies = generate_followup_question("book", missing, filters_updated)
        print(f"    Question: {question}")
        print(f"    Replies: {replies}")
        
        # Should NOT be asking about genre again!
        if "genre" in missing[0].lower():
            print(f"    [FAIL] LOOP DETECTED! Still asking about genre!")
            return False
        else:
            print(f"     Progressed to next question: {missing[0]}")
    
    # Turn 3: User responds with format
    print("\n  Turn 3: User says 'Paperback'")
    is_specific, extracted = is_specific_query("Paperback", filters_updated)
    
    if extracted.get("format"):
        filters_updated["format"] = extracted["format"]
    
    print(f"    Extracted: {extracted}")
    print(f"    Updated filters: format={filters_updated.get('format')}")
    
    should_ask, missing = should_ask_followup("Paperback", filters_updated)
    print(f"    Should ask followup? {should_ask}")
    print(f"    Missing info: {missing}")
    
    if should_ask and missing:
        question, replies = generate_followup_question("book", missing, filters_updated)
        print(f"    Question: {question}")
        print(f"    Replies: {replies[:3]}...")
        
        expected_third = "budget"
        if expected_third in missing[0].lower():
            print(f"     Third question is about {expected_third}")
        else:
            print(f"    [WARN]  Expected {expected_third}, got {missing[0]}")
    
    # Turn 4: User responds with budget
    print("\n  Turn 4: User says '$15-$25'")
    filters_updated["price_min_cents"] = 1500
    filters_updated["price_max_cents"] = 2500
    
    should_ask, missing = should_ask_followup("$15-$25", filters_updated)
    print(f"    Should ask followup? {should_ask}")
    print(f"    Missing info: {missing}")
    
    if not should_ask:
        print(f"     Interview complete! Ready to show recommendations")
        print(f"    Final filters: {filters_updated}")
        return True
    else:
        print(f"    [FAIL] Still asking questions: {missing}")
        return False


def main():
    """Run all book interview flow tests."""
    print("="*80)
    print("BOOK INTERVIEW FLOW VERIFICATION")
    print("="*80)
    
    results = {
        'Genre Extraction': test_genre_extraction(),
        'Format Extraction': test_format_extraction(),
        'Interview Progression': test_interview_progression(),
    }
    
    print("\n" + "="*80)
    print("TEST RESULTS")
    print("="*80)
    
    for test_name, passed in results.items():
        status = " PASS" if passed else "[FAIL] FAIL"
        print(f"{test_name:<30} {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "="*80)
    if all_passed:
        print(" ALL TESTS PASSED!")
        print("="*80)
        print("\n Book interview flow works correctly!")
        print("\nExpected Flow:")
        print("  1. Genre question → User selects Sci-Fi")
        print("  2. Format question → User selects Paperback")
        print("  3. Budget question → User selects $15-$25")
        print("  4. Show recommendations → Display books!")
        print("\n No more infinite loops!")
    else:
        print("[WARN]  SOME TESTS FAILED")
        print("="*80)
        print("\nReview failures above.")
    
    print("="*80)
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
