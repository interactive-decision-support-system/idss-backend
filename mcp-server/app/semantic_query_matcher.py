"""
Semantic Query Matcher - Uses embeddings to match queries to product categories.

Handles:
- Synonyms: "computer" → "laptop"
- Misspellings: "booksss" → "book", "computerrrs" → "computer"/"laptop"
- Semantic similarity: "notebook" → "laptop", "novel" → "book"
"""

from typing import Optional, Tuple, Dict
import re

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from app.structured_logger import StructuredLogger

logger = StructuredLogger("semantic_query_matcher")

# Category keywords and their semantic equivalents
CATEGORY_KEYWORDS = {
    "laptop": {
        "keywords": ["laptop", "laptops", "notebook", "notebooks", "macbook", "thinkpad", "xps", "chromebook"],
        "synonyms": ["computer", "computers", "pc", "pcs", "desktop", "desktops"],
        "target": "laptop"
    },
    "book": {
        "keywords": ["book", "books", "novel", "novels", "textbook", "textbooks", "reading"],
        "synonyms": ["literature", "story", "stories", "tome", "volume"],
        "target": "book"
    },
    "vehicle": {
        "keywords": ["car", "cars", "vehicle", "vehicles", "auto", "automobile", "truck", "suv", "sedan"],
        "synonyms": ["transport", "transportation", "ride"],
        "target": "vehicle"
    }
}

# Simple misspelling patterns (common typos)
MISSPELLING_PATTERNS = {
    "laptop": [r"lapt?op+s*", r"lapt?op+", r"lap?top+", r"comput?er+", r"comput?er+s*"],
    "book": [r"boo?k+s*", r"boo?k+", r"bo?ok+", r"bo?ok+s*"],
    "computer": [r"comput?er+", r"comput?er+s*", r"comp?uter+", r"comp?uter+s*"]
}


class SemanticQueryMatcher:
    """Matches user queries to product categories using semantic similarity."""
    
    def __init__(self, use_embeddings: bool = True):
        """
        Initialize semantic query matcher.
        
        Args:
            use_embeddings: Whether to use sentence transformers for semantic matching.
                          If False, uses keyword-based matching only.
        """
        self.use_embeddings = use_embeddings and SENTENCE_TRANSFORMERS_AVAILABLE
        self._encoder = None
        
        if self.use_embeddings:
            try:
                logger.info("loading_encoder", "Loading sentence transformer for semantic matching")
                self._encoder = SentenceTransformer("all-mpnet-base-v2")
                logger.info("encoder_loaded", "Encoder loaded successfully")
            except Exception as e:
                logger.warning("encoder_load_failed", f"Failed to load encoder: {e}", {"error": str(e)})
                self.use_embeddings = False
    
    def normalize_query(self, query: str) -> str:
        """
        Normalize query by fixing common misspellings.
        
        Args:
            query: User query string
            
        Returns:
            Normalized query string
        """
        normalized = query.lower().strip()
        
        # Fix common misspellings using regex patterns
        for category, patterns in MISSPELLING_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, normalized):
                    # Replace with correct spelling
                    if category == "laptop":
                        normalized = re.sub(pattern, "laptop", normalized, flags=re.IGNORECASE)
                    elif category == "book":
                        normalized = re.sub(pattern, "book", normalized, flags=re.IGNORECASE)
                    elif category == "computer":
                        normalized = re.sub(pattern, "computer", normalized, flags=re.IGNORECASE)
                    break
        
        return normalized
    
    def match_category_keywords(self, query: str) -> Optional[str]:
        """
        Match query to category using keyword matching.
        
        Args:
            query: User query string
            
        Returns:
            Matched category ("laptop", "book", "vehicle") or None
        """
        normalized = self.normalize_query(query)
        
        # Check each category
        for category, info in CATEGORY_KEYWORDS.items():
            # Check keywords
            for keyword in info["keywords"]:
                if keyword in normalized:
                    return info["target"]
            
            # Check synonyms
            for synonym in info["synonyms"]:
                if synonym in normalized:
                    return info["target"]
        
        return None
    
    def match_category_semantic(self, query: str, threshold: float = 0.6) -> Optional[Tuple[str, float]]:
        """
        Match query to category using semantic similarity (embeddings).
        
        Args:
            query: User query string
            threshold: Minimum similarity score (0-1)
            
        Returns:
            Tuple of (matched_category, similarity_score) or None
        """
        if not self.use_embeddings or self._encoder is None:
            return None
        
        try:
            query_embedding = self._encoder.encode(query, normalize_embeddings=True)
            
            best_match = None
            best_score = 0.0
            
            # Compare against category keywords
            for category, info in CATEGORY_KEYWORDS.items():
                # Create a representative text for this category
                category_text = " ".join(info["keywords"][:3])  # Use first 3 keywords
                category_embedding = self._encoder.encode(category_text, normalize_embeddings=True)
                
                # Compute cosine similarity
                similarity = np.dot(query_embedding, category_embedding)
                
                if similarity > best_score:
                    best_score = similarity
                    best_match = category
            
            if best_score >= threshold:
                return (CATEGORY_KEYWORDS[best_match]["target"], float(best_score))
            
        except Exception as e:
            logger.warning("semantic_match_failed", f"Semantic matching failed: {e}", {"error": str(e)})
        
        return None
    
    def match(self, query: str) -> Optional[str]:
        """
        Match query to product category using both keyword and semantic matching.
        
        Args:
            query: User query string
            
        Returns:
            Matched category ("laptop", "book", "vehicle") or None
        """
        if not query or not query.strip():
            return None
        
        # First try keyword matching (fast)
        keyword_match = self.match_category_keywords(query)
        if keyword_match:
            logger.info("keyword_match", f"Matched '{query}' to '{keyword_match}' via keywords")
            return keyword_match
        
        # Then try semantic matching (slower but handles synonyms/misspellings)
        semantic_match = self.match_category_semantic(query)
        if semantic_match:
            category, score = semantic_match
            logger.info("semantic_match", f"Matched '{query}' to '{category}' via semantic similarity (score: {score:.3f})")
            return category
        
        return None


# Global instance
_semantic_matcher = None

def get_semantic_matcher() -> SemanticQueryMatcher:
    """Get or create global semantic query matcher instance."""
    global _semantic_matcher
    if _semantic_matcher is None:
        _semantic_matcher = SemanticQueryMatcher()
    return _semantic_matcher
