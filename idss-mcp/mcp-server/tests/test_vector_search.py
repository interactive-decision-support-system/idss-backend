"""
Unit Tests for Vector Search Integration.

Tests semantic similarity search for all product types.
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.vector_search import UniversalEmbeddingStore, get_vector_store


class TestUniversalEmbeddingStore:
    """Test suite for Universal Embedding Store."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.store = UniversalEmbeddingStore(use_cache=False)
    
    def test_encode_text(self):
        """Test text encoding."""
        text = "laptop for video editing"
        embedding = self.store.encode_text(text)
        
        assert embedding.shape == (1, 768)  # all-mpnet-base-v2 dimension
        assert embedding.dtype == "float32"
    
    def test_encode_product(self):
        """Test product encoding."""
        product = {
            "product_id": "TEST-001",
            "name": "Dell XPS 15 Laptop",
            "description": "Powerful laptop for professionals",
            "category": "electronics",
            "brand": "Dell",
            "metadata": {"ram": "16GB", "storage": "512GB"}
        }
        
        embedding = self.store.encode_product(product)
        
        assert embedding.shape == (1, 768)
        assert embedding.dtype == "float32"
    
    def test_encode_product_minimal(self):
        """Test product encoding with minimal fields."""
        product = {
            "product_id": "TEST-002",
            "name": "Test Product"
        }
        
        embedding = self.store.encode_product(product)
        
        assert embedding.shape == (1, 768)
    
    def test_build_index(self):
        """Test index building."""
        products = [
            {
                "product_id": "TEST-001",
                "name": "Laptop",
                "description": "A great laptop",
                "category": "electronics",
                "brand": "Dell"
            },
            {
                "product_id": "TEST-002",
                "name": "Phone",
                "description": "Smartphone",
                "category": "electronics",
                "brand": "Apple"
            }
        ]
        
        self.store.build_index(products, save_index=False)
        
        assert self.store._index is not None
        assert len(self.store._product_ids) == 2
        assert self.store._index.ntotal == 2
    
    def test_search(self):
        """Test vector search."""
        # Build index first
        products = [
            {
                "product_id": "TEST-001",
                "name": "Dell XPS 15 Laptop",
                "description": "Powerful laptop for video editing",
                "category": "electronics",
                "brand": "Dell"
            },
            {
                "product_id": "TEST-002",
                "name": "iPhone 15 Pro",
                "description": "Premium smartphone",
                "category": "electronics",
                "brand": "Apple"
            }
        ]
        
        self.store.build_index(products, save_index=False)
        
        # Search
        product_ids, scores = self.store.search("laptop for video editing", k=2)
        
        assert len(product_ids) > 0
        assert len(scores) > 0
        assert len(product_ids) == len(scores)
        assert "TEST-001" in product_ids  # Should find laptop
        assert scores[0] > 0.0  # Similarity score should be positive
    
    def test_search_within_candidates(self):
        """Test search within candidate subset."""
        products = [
            {"product_id": "TEST-001", "name": "Laptop", "description": "Laptop"},
            {"product_id": "TEST-002", "name": "Phone", "description": "Phone"},
            {"product_id": "TEST-003", "name": "Tablet", "description": "Tablet"}
        ]
        
        self.store.build_index(products, save_index=False)
        
        # Search within subset
        candidates = ["TEST-001", "TEST-003"]
        product_ids, scores = self.store.search(
            "laptop",
            k=2,
            product_ids=candidates
        )
        
        assert len(product_ids) <= 2
        assert all(pid in candidates for pid in product_ids)
    
    def test_rank_products(self):
        """Test product ranking by similarity."""
        products = [
            {
                "product_id": "TEST-001",
                "name": "Dell XPS 15 Laptop",
                "description": "Laptop for video editing",
                "category": "electronics"
            },
            {
                "product_id": "TEST-002",
                "name": "iPhone",
                "description": "Smartphone",
                "category": "electronics"
            }
        ]
        
        ranked = self.store.rank_products(products, "laptop for video editing")
        
        assert len(ranked) == 2
        assert ranked[0]["product_id"] == "TEST-001"  # Laptop should rank higher
        assert "_vector_score" in ranked[0]
        assert ranked[0]["_vector_score"] > ranked[1]["_vector_score"]
    
    def test_empty_search(self):
        """Test search with no index."""
        store = UniversalEmbeddingStore()
        store._index = None
        
        product_ids, scores = store.search("test", k=5)
        
        assert product_ids == []
        assert scores == []
    
    def test_cache_embeddings(self):
        """Test embedding caching."""
        # Create store with caching enabled
        store = UniversalEmbeddingStore(use_cache=True)
        
        product = {
            "product_id": "TEST-001",
            "name": "Test Product",
            "description": "Test"
        }
        
        # First encode (should compute)
        embedding1 = store.encode_product(product)
        
        # Second encode (should use cache)
        embedding2 = store.encode_product(product)
        
        assert (embedding1 == embedding2).all()
        assert "TEST-001" in store._product_embeddings_cache


class TestVectorSearchIntegration:
    """Integration tests for vector search in MCP."""
    
    def test_vector_store_singleton(self):
        """Test that get_vector_store returns singleton."""
        store1 = get_vector_store()
        store2 = get_vector_store()
        
        assert store1 is store2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
