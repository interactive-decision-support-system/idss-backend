"""
LLM-based input validation and correction.

Uses Claude/GPT to intelligently detect and correct:
1. Misspellings and typos
2. Invalid/gibberish input
3. Intent understanding
"""

import os
import re
from typing import Tuple, Optional, Dict

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None


class LLMValidator:
    """LLM-based input validator using Claude."""
    
    def __init__(self):
        self.client = None
        self.enabled = False
        
        # Check if Anthropic is available
        if not ANTHROPIC_AVAILABLE:
            print("Info: Anthropic SDK not installed. Using fallback validation.")
            return
        
        # Try to initialize Anthropic client
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            try:
                self.client = Anthropic(api_key=api_key)
                self.enabled = True
                print("Info: LLM-based validation enabled with Claude.")
            except Exception as e:
                print(f"Warning: Could not initialize Anthropic client: {e}")
        else:
            print("Info: ANTHROPIC_API_KEY not set. Using fallback validation.")
    
    def validate_and_correct(self, user_input: str, context: Optional[str] = None) -> Dict[str, any]:
        """
        Validate user input and correct if needed.
        
        Returns:
            {
                "is_valid": bool,
                "corrected_input": str,
                "confidence": float,  # 0-1
                "detected_intent": str,  # "domain_selection", "filter", "gibberish", etc.
                "suggestions": List[str],
                "error_message": Optional[str]
            }
        """
        if not self.enabled:
            # Fallback to basic validation
            return self._basic_validation(user_input)
        
        try:
            prompt = self._build_validation_prompt(user_input, context)
            
            message = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=200,
                temperature=0,  # Deterministic
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = message.content[0].text
            return self._parse_llm_response(response_text, user_input)
            
        except Exception as e:
            print(f"LLM validation error: {e}")
            return self._basic_validation(user_input)
    
    def _build_validation_prompt(self, user_input: str, context: Optional[str]) -> str:
        """Build prompt for LLM validation."""
        prompt = f"""You are an intelligent input validator for an e-commerce chatbot that sells:
- VEHICLES (cars, trucks, SUVs, sedans, etc.)
- LAPTOPS (computers, notebooks, MacBooks, PCs, gaming laptops, etc.)
- BOOKS (novels, fiction, non-fiction, textbooks, ebooks, etc.)
- JEWELRY (necklaces, earrings, bracelets, rings, pendants, etc.)
- ACCESSORIES (scarves, hats, belts, bags, watches, sunglasses, etc.)

User input: "{user_input}"
Context: {context or "No active conversation"}

Your job: Understand what the user is trying to say, even with ANY misspelling, typo, or variation.

Analyze and respond in EXACT format:

VALID: [yes/no]
CORRECTED: [corrected text or original if no correction needed]
INTENT: [domain_selection/filter_response/gibberish/greeting/price/brand/use_case/genre/other]
CONFIDENCE: [0.0-1.0]
SUGGESTIONS: [comma-separated suggestions, or "none"]
ERROR: [error message if invalid, or "none"]

CRITICAL RULES:
1. Be lenient with RECOGNIZABLE misspellings but reject random gibberish
   - VALID misspellings (have clear phonetic/structural similarity):
     * "lapto", "lptop", "latop", "laptopp" → "laptop" (recognizable consonant structure)
     * "notbok", "noteboook", "ntbook", "notebk" → "notebook"
     * "bokss", "boook", "buk", "bok" → "book"
     * "vehical", "vehicl", "vehecle", "vhicle" → "vehicle"
     * "compter", "computor", "compuer" → "computer"
   
   - INVALID gibberish (reject these - too short, no structure, random):
     * "ug", "gu", "xy", "qp", "zx" → REJECT (2 letters, no recognizable pattern)
     * "asdf", "qwerty", "xyz", "fff" → REJECT (keyboard mashing or repetition)
     * "zzz", "aaa", "mmm" → REJECT (just repeated letters)
     * Random 2-3 letter combos with no vowels/structure → REJECT

2. For 3+ letter inputs, check if there's ANY similarity to our products:
   - Does it share 50%+ letters with laptop/book/vehicle/car/computer/jewelry/accessories?
   - Does it have a vowel pattern that could match?
   - If NO to both → REJECT as gibberish
   
3. Greetings are VALID (hi, hello, hey, yo) - trigger domain selection

4. Prices are ALWAYS VALID ($500, 700-1200, $15-$30)

5. In context (active conversation), accept:
   - Brand names (Dell, Apple, Lenovo, HP, Samsung, etc.)
   - Use cases (Gaming, Work, School, Creative, Business, etc.)
   - Genres (Fiction, Mystery, Sci-Fi, Non-Fiction, Romance, etc.)
   - Formats (Hardcover, Paperback, E-book, Audiobook, etc.)
   - Short responses (yes, no, ok, maybe, etc.)

EXAMPLES:

Input: "laaaaptop" (user held down key)
VALID: yes
CORRECTED: laptop
INTENT: domain_selection
CONFIDENCE: 0.95
SUGGESTIONS: none
ERROR: none

Input: "notebooook" (extra o's)
VALID: yes
CORRECTED: notebook
INTENT: domain_selection
CONFIDENCE: 0.90
SUGGESTIONS: none
ERROR: none

Input: "cpmputer" (swap typo)
VALID: yes
CORRECTED: computer
INTENT: domain_selection
CONFIDENCE: 0.85
SUGGESTIONS: none
ERROR: none

Input: "ug" (too short, no recognizable structure)
VALID: no
CORRECTED: ug
INTENT: gibberish
CONFIDENCE: 0.95
SUGGESTIONS: Cars,Laptops,Books,Phones
ERROR: I didn't understand that. Please tell me what you're looking for.

Input: "gu" (too short, random letters)
VALID: no
CORRECTED: gu
INTENT: gibberish
CONFIDENCE: 0.95
SUGGESTIONS: Cars,Laptops,Books,Phones
ERROR: I didn't understand that. Please tell me what you're looking for.

Input: "xyz" (keyboard gibberish)
VALID: no
CORRECTED: xyz
INTENT: gibberish
CONFIDENCE: 0.98
SUGGESTIONS: Cars,Laptops,Books,Phones
ERROR: I didn't understand that. Please tell me what you're looking for.

Input: "asdfghjkl" (keyboard mashing)
VALID: no
CORRECTED: asdfghjkl
INTENT: gibberish
CONFIDENCE: 0.99
SUGGESTIONS: Cars,Laptops,Books,Phones
ERROR: I didn't understand that. Please tell me what you're looking for.

Input: "vheicle" (recognizable typo)
VALID: yes
CORRECTED: vehicle
INTENT: domain_selection
CONFIDENCE: 0.88
SUGGESTIONS: none
ERROR: none

Input: "buk" (short but recognizable as book)
VALID: yes
CORRECTED: book
INTENT: domain_selection
CONFIDENCE: 0.75
SUGGESTIONS: none
ERROR: none

Input: "phones" or "Phones"
VALID: yes
CORRECTED: phones
INTENT: domain_selection
CONFIDENCE: 1.0
SUGGESTIONS: none
ERROR: none

Now analyze: "{user_input}"
"""
        return prompt
    
    def _parse_llm_response(self, response: str, original_input: str) -> Dict[str, any]:
        """Parse LLM response into structured format."""
        lines = response.strip().split('\n')
        result = {
            "is_valid": True,
            "corrected_input": original_input,
            "confidence": 0.5,
            "detected_intent": "other",
            "suggestions": [],
            "error_message": None
        }
        
        for line in lines:
            line = line.strip()
            if line.startswith("VALID:"):
                result["is_valid"] = "yes" in line.lower()
            elif line.startswith("CORRECTED:"):
                corrected = line.split(":", 1)[1].strip()
                if corrected and corrected.lower() != "none":
                    result["corrected_input"] = corrected
            elif line.startswith("INTENT:"):
                result["detected_intent"] = line.split(":", 1)[1].strip()
            elif line.startswith("CONFIDENCE:"):
                try:
                    result["confidence"] = float(line.split(":", 1)[1].strip())
                except:
                    pass
            elif line.startswith("SUGGESTIONS:"):
                sugg = line.split(":", 1)[1].strip()
                if sugg and sugg.lower() != "none":
                    result["suggestions"] = [s.strip() for s in sugg.split(",")]
            elif line.startswith("ERROR:"):
                err = line.split(":", 1)[1].strip()
                if err and err.lower() != "none":
                    result["error_message"] = err
        
        return result
    
    def _basic_validation(self, user_input: str) -> Dict[str, any]:
        """Fallback basic validation when LLM is unavailable."""
        normalized = user_input.lower().strip()
        
        # Check for valid price patterns first (these have no letters but are valid)
        price_patterns = [
            r'\$\d+',  # $500
            r'\d+\s*[-–]\s*\$?\d+',  # 700-1200, $700-1200
            r'\$\d+\s*[-–]\s*\$\d+',  # $700-$1200
        ]
        is_price = any(re.search(p, user_input) for p in price_patterns)
        
        if is_price:
            return {
                "is_valid": True,
                "corrected_input": user_input,
                "confidence": 1.0,
                "detected_intent": "price",
                "suggestions": [],
                "error_message": None
            }
        
        # Check for greetings (before gibberish check)
        greetings = ["hi", "hello", "hey", "yo"]
        if normalized in greetings:
            return {
                "is_valid": True,
                "corrected_input": user_input,
                "confidence": 1.0,
                "detected_intent": "greeting",
                "suggestions": ["Cars", "Laptops", "Books", "Phones"],
                "error_message": None
            }
        
        # Check for gibberish - STRICTER rules
        # Reject if:
        # 1. Less than 3 characters (unless it's a known greeting)
        # 2. No letters at all
        # 3. Only special characters
        # 4. Repeated single character (aaa, zzz)
        is_gibberish = (
            len(normalized) < 3 or  # Changed from < 2 to < 3
            not re.search(r'[a-zA-Z]', user_input) or
            bool(re.match(r'^[^a-zA-Z0-9$-]+$', normalized)) or
            bool(re.match(r'^(.)\1+$', normalized))  # Repeated char like "aaa"
        )
        
        # Additional check: keyboard mashing patterns
        keyboard_patterns = ['asdf', 'qwer', 'zxcv', 'jkl', 'fgh']
        if any(pattern in normalized for pattern in keyboard_patterns):
            is_gibberish = True
        
        if is_gibberish:
            return {
                "is_valid": False,
                "corrected_input": user_input,
                "confidence": 0.9,
                "detected_intent": "gibberish",
                "suggestions": ["Cars", "Laptops", "Books", "Phones"],
                "error_message": "I didn't understand that. Please tell me what you're looking for."
            }
        
        # Check for recognizable product keywords (lenient matching)
        product_keywords = [
            'laptop', 'book', 'vehicle', 'car', 'computer', 'notebook', 'pc', 'mac', 'suv', 'truck', 'sedan',
            'phone', 'phones', 'smartphone', 'fairphone'
        ]
        if any(keyword in normalized for keyword in product_keywords):
            return {
                "is_valid": True,
                "corrected_input": user_input,
                "confidence": 0.9,
                "detected_intent": "domain_selection",
                "suggestions": [],
                "error_message": None
            }
        
        # Check for similarity to known brands (common ones)
        known_brands = [
            'apple', 'dell', 'hp', 'lenovo', 'asus', 'acer', 'samsung', 'microsoft',
            'ford', 'toyota', 'honda', 'bmw', 'tesla', 'chevrolet', 'nissan',
            'pandora', 'tiffany', 'swarovski', 'kay', 'zales', 'jared',
        ]
        if any(brand in normalized for brand in known_brands):
            return {
                "is_valid": True,
                "corrected_input": user_input,
                "confidence": 0.9,
                "detected_intent": "brand",
                "suggestions": [],
                "error_message": None
            }
        
        # Check for common use cases, control terms, book/laptop answers, and post-recommendation phrases
        common_terms = ['gaming', 'work', 'school', 'business', 'creative', 'home', 'office',
                       'fiction', 'mystery', 'romance', 'fantasy', 'thriller', 'scifi',
                       'paperback', 'hardcover', 'ebook', 'e-book', 'audiobook',
                       'restart', 'reset', 'clear', 'start', 'begin',
                       'similar', 'compare', 'checkout', 'recommendations', 'items',
                       'no preference', 'jewelers', 'any price']
        if any(term in normalized for term in common_terms):
            return {
                "is_valid": True,
                "corrected_input": user_input,
                "confidence": 0.85,
                "detected_intent": "use_case" if normalized not in ['restart', 'reset', 'clear', 'start', 'begin'] else "other",
                "suggestions": [],
                "error_message": None
            }
        
        # Advanced gibberish detection for longer inputs
        # Check if input looks like random letters with no recognizable pattern
        if len(normalized) >= 3:
            # Calculate letter frequency anomalies
            vowels = set('aeiou')
            consonants = set('bcdfghjklmnpqrstvwxyz')
            
            vowel_count = sum(1 for c in normalized if c in vowels)
            consonant_count = sum(1 for c in normalized if c in consonants)
            total_letters = vowel_count + consonant_count
            
            if total_letters > 0:
                vowel_ratio = vowel_count / total_letters
                
                # Check for too many consonants in a row (5+ consonants with no vowels)
                max_consonants = 0
                current_consonants = 0
                for c in normalized:
                    if c in consonants:
                        current_consonants += 1
                        max_consonants = max(max_consonants, current_consonants)
                    else:
                        current_consonants = 0
                
                # Gibberish indicators - ONLY reject if:
                # 1. No vowels at all AND more than 3 letters (e.g., "hfhf", "xyz")
                # 2. Less than 10% vowels AND 6+ consonants in a row
                # 3. Vowel pattern is extremely abnormal (alternating single letters with no structure)
                if (vowel_count == 0 and len(normalized) > 3) or \
                   (vowel_ratio < 0.10 and max_consonants >= 6):
                    return {
                        "is_valid": False,
                        "corrected_input": user_input,
                        "confidence": 0.85,
                        "detected_intent": "gibberish",
                        "suggestions": ["Cars", "Laptops", "Books", "Phones"],
                        "error_message": "I didn't understand that. Please tell me what you're looking for."
                    }
                
        # Final heuristic: Check for similarity to known valid terms
        # Use STRICTER matching - need meaningful overlap
        if len(normalized) >= 3:
            valid_terms = ['laptop', 'book', 'vehicle', 'car', 'computer', 'notebook', 
                          'lapto', 'vehicl', 'comp', 'note', 'lap', 'top', 'auto',
                          'gaming', 'work', 'school', 'business', 'fiction', 'novel',
                          'truck', 'suv', 'sedan', 'dell', 'apple', 'hp', 'lenovo']
            
            # For inputs 3 chars: must match a valid 3-char term exactly or be within edit distance 1
            if len(normalized) == 3:
                three_char_valid = ['car', 'suv', 'lap', 'top', 'mac', 'buk', 'bok']  # Added common typos
                if normalized not in three_char_valid:
                    # Check edit distance 1 (one substitution) - only compare same length strings
                    has_close_match = False
                    for term in three_char_valid:
                        if len(term) == 3:  # Ensure same length
                            diff_count = sum(1 for i in range(3) if normalized[i] != term[i])
                            if diff_count <= 1:
                                has_close_match = True
                                break
                    
                    if not has_close_match:
                        return {
                            "is_valid": False,
                            "corrected_input": user_input,
                            "confidence": 0.80,
                            "detected_intent": "gibberish",
                            "suggestions": ["Cars", "Laptops", "Books", "Phones"],
                            "error_message": "I didn't understand that. Please tell me what you're looking for."
                        }
            
            # For longer inputs (4+ chars): need multiple matching substrings OR one long match
            elif len(normalized) >= 4:
                # Count how many 2-char substrings match ANY valid term
                matching_substrings = 0
                for i in range(len(normalized) - 1):
                    substring = normalized[i:i+2]
                    for term in valid_terms:
                        if substring in term:
                            matching_substrings += 1
                            break  # Count this substring only once
                
                # Also check for 3-char substrings (stronger signal)
                long_match = False
                if len(normalized) >= 5:
                    for i in range(len(normalized) - 2):
                        substring = normalized[i:i+3]
                        for term in valid_terms:
                            if substring in term:
                                long_match = True
                                break
                        if long_match:
                            break
                
                # Reject if: less than 40% of possible substrings match AND no 3-char match
                possible_substrings = len(normalized) - 1
                match_ratio = matching_substrings / possible_substrings if possible_substrings > 0 else 0
                
                if match_ratio < 0.4 and not long_match:
                    return {
                        "is_valid": False,
                        "corrected_input": user_input,
                        "confidence": 0.75,
                        "detected_intent": "gibberish",
                        "suggestions": ["Cars", "Laptops", "Books", "Phones"],
                        "error_message": "I didn't understand that. Please tell me what you're looking for."
                    }
        
        # Default: ACCEPT with low confidence
        # For inputs we're not sure about, let them through (they might be typos or valid terms)
        return {
            "is_valid": True,
            "corrected_input": user_input,
            "confidence": 0.4,
            "detected_intent": "other",
            "suggestions": [],
            "error_message": None
        }


# Global validator instance
_validator = None

def get_llm_validator() -> LLMValidator:
    """Get singleton LLM validator instance."""
    global _validator
    if _validator is None:
        _validator = LLMValidator()
    return _validator
