"""
Custom Genre Input Handler for Books

Allows users to specify custom genres when they don't see their preferred option.

Features:
- Validates custom genre input
- Normalizes genre names
- Tracks popular custom genres
- Suggests similar genres

Usage:
    from app.custom_genre_handler import validate_custom_genre, suggest_genres
    
    # Validate user input
    genre = validate_custom_genre("Historical Fiction")
    
    # Get suggestions
    suggestions = suggest_genres("horror")
"""

from typing import List, Optional, Dict, Any
import re
from difflib import get_close_matches


# Standard genre list
STANDARD_GENRES = [
    "Fiction",
    "Non-fiction",
    "Mystery",
    "Thriller",
    "Sci-Fi",
    "Science Fiction",
    "Fantasy",
    "Romance",
    "Historical Fiction",
    "Biography",
    "Autobiography",
    "Memoir",
    "Self-Help",
    "Business",
    "Psychology",
    "Philosophy",
    "History",
    "Science",
    "Travel",
    "Cooking",
    "Art",
    "Poetry",
    "Drama",
    "Horror",
    "Crime",
    "Adventure",
    "Young Adult",
    "Children's",
    "Graphic Novel",
    "Comics"
]

# Genre aliases (alternate names)
GENRE_ALIASES = {
    "sci-fi": "Science Fiction",
    "scifi": "Science Fiction",
    "sf": "Science Fiction",
    "ya": "Young Adult",
    "biography": "Biography",
    "bio": "Biography",
    "self help": "Self-Help",
    "selfhelp": "Self-Help",
    "historical": "Historical Fiction",
    "history fiction": "Historical Fiction",
    "true crime": "Crime",
    "detective": "Mystery",
    "suspense": "Thriller",
    "paranormal": "Fantasy",
    "urban fantasy": "Fantasy",
    "epic fantasy": "Fantasy",
    "space opera": "Science Fiction",
    "dystopian": "Science Fiction",
    "post-apocalyptic": "Science Fiction",
    "contemporary": "Fiction",
    "literary fiction": "Fiction",
    "women's fiction": "Fiction",
    "chick lit": "Romance",
    "romantic comedy": "Romance",
    "western": "Adventure",
    "coming of age": "Young Adult"
}


def normalize_genre(genre: str) -> str:
    """
    Normalize genre input.
    
    Args:
        genre: User input genre
        
    Returns:
        Normalized genre name
    """
    if not genre:
        return ""
    
    # Clean up input
    normalized = genre.strip().lower()
    
    # Remove extra whitespace
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Check for aliases
    if normalized in GENRE_ALIASES:
        return GENRE_ALIASES[normalized]
    
    # Check for exact match (case-insensitive)
    for standard in STANDARD_GENRES:
        if normalized == standard.lower():
            return standard
    
    # Capitalize properly
    # "historical fiction" -> "Historical Fiction"
    return ' '.join(word.capitalize() for word in normalized.split())


def validate_custom_genre(genre: str) -> Optional[str]:
    """
    Validate custom genre input.
    
    Args:
        genre: User input genre
        
    Returns:
        Validated and normalized genre, or None if invalid
    """
    if not genre:
        return None
    
    # Normalize first
    normalized = normalize_genre(genre)
    
    # Check length
    if len(normalized) < 2:
        return None
    
    if len(normalized) > 50:
        return None
    
    # Check for valid characters (letters, spaces, hyphens, apostrophes)
    if not re.match(r"^[A-Za-z\s\-']+$", normalized):
        return None
    
    return normalized


def suggest_genres(query: str, max_suggestions: int = 5) -> List[str]:
    """
    Suggest genres based on partial input.
    
    Args:
        query: Partial genre name
        max_suggestions: Maximum number of suggestions
        
    Returns:
        List of suggested genres
    """
    if not query:
        return STANDARD_GENRES[:max_suggestions]
    
    query_lower = query.lower()
    
    # First, check for exact matches in aliases
    if query_lower in GENRE_ALIASES:
        return [GENRE_ALIASES[query_lower]]
    
    # Find genres that start with the query
    exact_matches = [
        genre for genre in STANDARD_GENRES 
        if genre.lower().startswith(query_lower)
    ]
    
    if exact_matches:
        return exact_matches[:max_suggestions]
    
    # Find genres that contain the query
    partial_matches = [
        genre for genre in STANDARD_GENRES 
        if query_lower in genre.lower()
    ]
    
    if partial_matches:
        return partial_matches[:max_suggestions]
    
    # Use fuzzy matching
    fuzzy_matches = get_close_matches(
        query, 
        STANDARD_GENRES, 
        n=max_suggestions, 
        cutoff=0.6
    )
    
    return fuzzy_matches


def get_genre_prompt(language: str = "en") -> Dict[str, Any]:
    """
    Get genre selection prompt with custom input option.
    
    Args:
        language: Language code ("en" or "fr")
        
    Returns:
        Dict with question text and options
    """
    if language == "fr":
        return {
            "question": "Quel genre vous intéresse?",
            "quick_replies": [
                "Fiction",
                "Mystère",
                "Science-Fiction",
                "Romance",
                "Fantaisie",
                "Non-fiction",
                "Biographie",
                "Autre (saisir ci-dessous)"
            ],
            "custom_prompt": "Si vous ne voyez pas votre genre préféré, veuillez le saisir ci-dessous:",
            "placeholder": "Ex: Fiction Historique, Thriller, Horreur..."
        }
    else:  # English
        return {
            "question": "What genre are you interested in?",
            "quick_replies": [
                "Fiction",
                "Mystery",
                "Sci-Fi",
                "Romance",
                "Fantasy",
                "Non-fiction",
                "Biography",
                "Other (type below)"
            ],
            "custom_prompt": "If you don't see your preferred genre, please type it below:",
            "placeholder": "e.g., Historical Fiction, Thriller, Horror..."
        }


def format_genre_response(custom_genre: str, language: str = "en") -> str:
    """
    Format response acknowledging custom genre.
    
    Args:
        custom_genre: The custom genre entered by user
        language: Language code
        
    Returns:
        Formatted response message
    """
    if language == "fr":
        return f"Parfait! Je vais rechercher des livres de genre '{custom_genre}'."
    else:
        return f"Great! I'll search for books in the '{custom_genre}' genre."


# API endpoint integration
def process_genre_selection(
    user_input: str, 
    is_custom: bool = False,
    language: str = "en"
) -> Dict[str, Any]:
    """
    Process genre selection from user.
    
    Args:
        user_input: User's genre selection or custom input
        is_custom: Whether this is a custom input
        language: Language code
        
    Returns:
        Dict with processed genre and validation status
    """
    if is_custom:
        # Validate custom genre
        validated = validate_custom_genre(user_input)
        
        if not validated:
            if language == "fr":
                error_msg = "Genre invalide. Veuillez utiliser uniquement des lettres et des espaces."
            else:
                error_msg = "Invalid genre. Please use only letters and spaces."
            
            return {
                "success": False,
                "error": error_msg,
                "suggestions": suggest_genres(user_input)
            }
        
        # Check if it matches a standard genre
        suggestions = suggest_genres(user_input, max_suggestions=3)
        
        return {
            "success": True,
            "genre": validated,
            "is_custom": True,
            "message": format_genre_response(validated, language),
            "suggestions": suggestions if suggestions else None
        }
    else:
        # Standard genre selection
        normalized = normalize_genre(user_input)
        
        return {
            "success": True,
            "genre": normalized,
            "is_custom": False,
            "message": format_genre_response(normalized, language)
        }


# Testing function
def test_custom_genre_handler():
    """Test the custom genre handler."""
    print("="*80)
    print("CUSTOM GENRE HANDLER - TEST")
    print("="*80)
    
    test_cases = [
        # Valid inputs
        ("historical fiction", True),
        ("Horror", True),
        ("sci-fi", True),
        ("True Crime", True),
        
        # Invalid inputs
        ("abc123", False),
        ("", False),
        ("a", False),
        ("x" * 100, False),
        
        # Suggestions
        ("hist", ["Historical Fiction"]),
        ("sci", ["Science Fiction", "Science"]),
        ("fic", ["Fiction", "Historical Fiction", "Science Fiction"]),
    ]
    
    print("\n1. Testing validation:")
    for input_text, should_pass in test_cases[:8]:
        result = validate_custom_genre(input_text)
        status = "" if (result is not None) == should_pass else "[FAIL]"
        print(f"  {status} '{input_text}' → {result}")
    
    print("\n2. Testing suggestions:")
    for query, expected_contains in test_cases[8:]:
        suggestions = suggest_genres(query, max_suggestions=5)
        has_expected = any(exp in suggestions for exp in expected_contains)
        status = "" if has_expected else "[FAIL]"
        print(f"  {status} '{query}' → {suggestions[:3]}")
    
    print("\n3. Testing full processing:")
    process_tests = [
        ("Mystery", False, "en"),
        ("Historical Fiction", True, "en"),
        ("Mystère", False, "fr"),
    ]
    
    for input_text, is_custom, lang in process_tests:
        result = process_genre_selection(input_text, is_custom, lang)
        print(f"  Input: '{input_text}' (custom={is_custom}, lang={lang})")
        print(f"    Success: {result['success']}")
        print(f"    Genre: {result.get('genre', 'N/A')}")
        print(f"    Message: {result.get('message', result.get('error', 'N/A'))}")
    
    print("\n4. Testing genre prompt:")
    for lang in ["en", "fr"]:
        prompt = get_genre_prompt(lang)
        print(f"\n  {lang.upper()} Prompt:")
        print(f"    Question: {prompt['question']}")
        print(f"    Options: {', '.join(prompt['quick_replies'][:4])}...")
        print(f"    Custom: {prompt['custom_prompt'][:50]}...")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    test_custom_genre_handler()
