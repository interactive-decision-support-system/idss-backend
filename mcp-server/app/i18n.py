"""
Internationalization (i18n) Support for MCP Server

Supports multiple languages for:
- Interview questions
- Product categories
- Filters and constraints
- Response messages

Currently supported languages:
- English (en) - default
- French (fr)

Usage:
    from app.i18n import translate, get_language
    
    # Translate a key
    text = translate("question.budget", lang="fr")
    
    # Get current language from request
    lang = get_language(request_headers)
"""

from typing import Dict, Any, Optional, List
from enum import Enum


class Language(Enum):
    """Supported languages."""
    ENGLISH = "en"
    FRENCH = "fr"


# Translation dictionary
TRANSLATIONS = {
    # Questions
    "question.budget": {
        "en": "What's your budget range?",
        "fr": "Quelle est votre fourchette de budget?"
    },
    "question.use_case": {
        "en": "What will you primarily use this for?",
        "fr": "À quoi allez-vous principalement l'utiliser?"
    },
    "question.brand_preference": {
        "en": "Do you have any brand preferences?",
        "fr": "Avez-vous des préférences de marque?"
    },
    "question.laptop.budget": {
        "en": "What's your budget range for the laptop? Feel free to also share what you'll primarily use the laptop for or what body style you prefer.",
        "fr": "Quelle est votre fourchette de budget pour l'ordinateur portable? N'hésitez pas à partager également à quoi vous utiliserez principalement l'ordinateur portable ou quel style de corps vous préférez."
    },
    "question.book.genre": {
        "en": "What genre are you interested in?",
        "fr": "Quel genre vous intéresse?"
    },
    "question.book.genre.custom": {
        "en": "If you don't see your preferred genre, please type it below:",
        "fr": "Si vous ne voyez pas votre genre préféré, veuillez le saisir ci-dessous:"
    },
    
    # Categories
    "category.electronics": {
        "en": "Electronics",
        "fr": "Électronique"
    },
    "category.books": {
        "en": "Books",
        "fr": "Livres"
    },
    "category.clothing": {
        "en": "Clothing",
        "fr": "Vêtements"
    },
    "category.home": {
        "en": "Home",
        "fr": "Maison"
    },
    "category.automotive": {
        "en": "Automotive",
        "fr": "Automobile"
    },
    
    # Product Types
    "product_type.laptop": {
        "en": "Laptop",
        "fr": "Ordinateur portable"
    },
    "product_type.gaming_laptop": {
        "en": "Gaming Laptop",
        "fr": "Ordinateur portable de jeu"
    },
    "product_type.desktop_pc": {
        "en": "Desktop PC",
        "fr": "PC de bureau"
    },
    "product_type.phone": {
        "en": "Phone",
        "fr": "Téléphone"
    },
    "product_type.tablet": {
        "en": "Tablet",
        "fr": "Tablette"
    },
    "product_type.book": {
        "en": "Book",
        "fr": "Livre"
    },
    
    # Use Cases / Subcategories
    "use_case.gaming": {
        "en": "Gaming",
        "fr": "Jeux"
    },
    "use_case.work": {
        "en": "Work",
        "fr": "Travail"
    },
    "use_case.school": {
        "en": "School",
        "fr": "École"
    },
    "use_case.creative": {
        "en": "Creative",
        "fr": "Créatif"
    },
    
    # Book Genres
    "genre.fiction": {
        "en": "Fiction",
        "fr": "Fiction"
    },
    "genre.mystery": {
        "en": "Mystery",
        "fr": "Mystère"
    },
    "genre.scifi": {
        "en": "Sci-Fi",
        "fr": "Science-Fiction"
    },
    "genre.romance": {
        "en": "Romance",
        "fr": "Romance"
    },
    "genre.fantasy": {
        "en": "Fantasy",
        "fr": "Fantaisie"
    },
    "genre.nonfiction": {
        "en": "Non-fiction",
        "fr": "Non-fiction"
    },
    "genre.biography": {
        "en": "Biography",
        "fr": "Biographie"
    },
    
    # Budget Options
    "budget.under_500": {
        "en": "Under $500",
        "fr": "Moins de 500$"
    },
    "budget.500_1000": {
        "en": "$500-$1,000",
        "fr": "500$-1 000$"
    },
    "budget.1000_1500": {
        "en": "$1,000-$1,500",
        "fr": "1 000$-1 500$"
    },
    "budget.over_1500": {
        "en": "Over $1,500",
        "fr": "Plus de 1 500$"
    },
    
    # Filters
    "filter.brand": {
        "en": "Brand",
        "fr": "Marque"
    },
    "filter.price": {
        "en": "Price",
        "fr": "Prix"
    },
    "filter.color": {
        "en": "Color",
        "fr": "Couleur"
    },
    "filter.rating": {
        "en": "Rating",
        "fr": "Note"
    },
    
    # Response Messages
    "response.found_products": {
        "en": "Found {count} products",
        "fr": "Trouvé {count} produits"
    },
    "response.no_products": {
        "en": "No products found matching your criteria",
        "fr": "Aucun produit trouvé correspondant à vos critères"
    },
    "response.error": {
        "en": "An error occurred. Please try again.",
        "fr": "Une erreur s'est produite. Veuillez réessayer."
    },
    
    # Common Actions
    "action.search": {
        "en": "Search",
        "fr": "Rechercher"
    },
    "action.filter": {
        "en": "Filter",
        "fr": "Filtrer"
    },
    "action.sort": {
        "en": "Sort",
        "fr": "Trier"
    },
    "action.add_to_cart": {
        "en": "Add to Cart",
        "fr": "Ajouter au panier"
    },
}


def translate(
    key: str,
    lang: str = "en",
    params: Optional[Dict[str, Any]] = None,
    default: Optional[str] = None
) -> str:
    """
    Translate a key to the specified language.
    
    Args:
        key: Translation key (e.g., "question.budget")
        lang: Language code ("en", "fr")
        params: Optional parameters for string formatting
        default: Default text if key not found
        
    Returns:
        Translated string
        
    Examples:
        >>> translate("question.budget", lang="fr")
        "Quelle est votre fourchette de budget?"
        
        >>> translate("response.found_products", lang="fr", params={"count": 5})
        "Trouvé 5 produits"
    """
    # Normalize language code
    lang = lang.lower()[:2]  # Take first 2 chars
    
    # Get translation
    translation_dict = TRANSLATIONS.get(key, {})
    text = translation_dict.get(lang, translation_dict.get("en", default or key))
    
    # Apply parameters if provided
    if params:
        try:
            text = text.format(**params)
        except (KeyError, ValueError):
            pass  # Return unformatted text if params don't match
    
    return text


def get_language(headers: Optional[Dict[str, str]] = None) -> str:
    """
    Detect language from HTTP headers.
    
    Args:
        headers: HTTP request headers
        
    Returns:
        Language code ("en" or "fr")
    """
    if not headers:
        return "en"
    
    # Check Accept-Language header
    accept_language = headers.get("Accept-Language", "").lower()
    
    if "fr" in accept_language:
        return "fr"
    
    return "en"


def translate_categories(lang: str = "en") -> Dict[str, str]:
    """
    Get translated category names.
    
    Args:
        lang: Language code
        
    Returns:
        Dictionary mapping category keys to translated names
    """
    categories = [
        "electronics",
        "books",
        "clothing",
        "home",
        "automotive"
    ]
    
    return {
        cat: translate(f"category.{cat}", lang)
        for cat in categories
    }


def translate_genres(lang: str = "en") -> Dict[str, str]:
    """
    Get translated genre names for books.
    
    Args:
        lang: Language code
        
    Returns:
        Dictionary mapping genre keys to translated names
    """
    genres = [
        "fiction",
        "mystery",
        "scifi",
        "romance",
        "fantasy",
        "nonfiction",
        "biography"
    ]
    
    return {
        genre: translate(f"genre.{genre}", lang)
        for genre in genres
    }


def translate_budget_options(lang: str = "en") -> List[str]:
    """
    Get translated budget options.
    
    Args:
        lang: Language code
        
    Returns:
        List of translated budget options
    """
    budget_keys = [
        "budget.under_500",
        "budget.500_1000",
        "budget.1000_1500",
        "budget.over_1500"
    ]
    
    return [translate(key, lang) for key in budget_keys]


def translate_use_cases(lang: str = "en") -> Dict[str, str]:
    """
    Get translated use case names.
    
    Args:
        lang: Language code
        
    Returns:
        Dictionary mapping use case keys to translated names
    """
    use_cases = [
        "gaming",
        "work",
        "school",
        "creative"
    ]
    
    return {
        use_case: translate(f"use_case.{use_case}", lang)
        for use_case in use_cases
    }


# Utility function to add translations dynamically
def add_translation(key: str, translations: Dict[str, str]):
    """
    Add a new translation at runtime.
    
    Args:
        key: Translation key
        translations: Dictionary of language codes to translations
        
    Example:
        add_translation("greeting.hello", {
            "en": "Hello",
            "fr": "Bonjour"
        })
    """
    TRANSLATIONS[key] = translations


# Test function
def test_translations():
    """Test translation functionality."""
    print("="*80)
    print("TESTING TRANSLATIONS")
    print("="*80)
    
    test_cases = [
        ("question.budget", "en"),
        ("question.budget", "fr"),
        ("category.electronics", "fr"),
        ("product_type.laptop", "fr"),
        ("response.found_products", "fr", {"count": 5}),
        ("genre.scifi", "fr"),
    ]
    
    for test in test_cases:
        key = test[0]
        lang = test[1]
        params = test[2] if len(test) > 2 else None
        
        result = translate(key, lang, params)
        print(f"{key} ({lang}): {result}")
    
    print("\n" + "="*80)
    print("Categories in French:")
    for cat, name in translate_categories("fr").items():
        print(f"  {cat}: {name}")
    
    print("\nGenres in French:")
    for genre, name in translate_genres("fr").items():
        print(f"  {genre}: {name}")
    
    print("\nBudget Options in French:")
    for option in translate_budget_options("fr"):
        print(f"  {option}")


if __name__ == "__main__":
    test_translations()
