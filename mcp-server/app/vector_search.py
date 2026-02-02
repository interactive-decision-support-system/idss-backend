"""
Universal Vector Search for MCP - All Product Types.

Reuses IDSS embedding code but adapts it for universal product search.
Supports: vehicles, e-commerce, real estate, travel, and any future product types.
"""

import pickle
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import numpy as np
from datetime import datetime

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from app.structured_logger import StructuredLogger

logger = StructuredLogger("vector_search")


class UniversalEmbeddingStore:
    """
    Universal embedding store for all product types.
    
    Reuses IDSS DenseEmbeddingStore pattern but works with:
    - Vehicles (VINs)
    - E-commerce products (PROD-*)
    - Real Estate (PROP-*)
    - Travel (TRAVEL-*)
    - Any product type
    
    Usage:
        store = UniversalEmbeddingStore()
        product_ids, scores = store.search("laptop for video editing", k=10)
    """
    
    def __init__(
        self,
        model_name: str = "all-mpnet-base-v2",
        index_type: str = "Flat",
        use_cache: bool = True
    ):
        """
        Initialize universal embedding store.
        
        Args:
            model_name: Sentence transformer model name
            index_type: FAISS index type (Flat or IVF)
            use_cache: Whether to use cached embeddings/index
        """
        self.model_name = model_name
        self.index_type = index_type
        self.use_cache = use_cache
        
        # Lazy-loaded components
        self._encoder = None
        self._index = None
        self._product_ids = []
        self._product_id_to_idx = {}
        self._product_embeddings_cache = {}  # product_id -> embedding
        
        # Index directory (for caching)
        self.index_dir = Path(__file__).parent.parent / "vector_indices"
        self.index_dir.mkdir(exist_ok=True)
        
        logger.info("vector_store_init", "Initializing vector store", {
            "model_name": model_name,
            "index_type": index_type,
            "use_cache": use_cache
        })
        
        # Try to load existing index on initialization
        if use_cache:
            self._load_index()
    
    def _get_encoder(self):
        """Lazy load sentence transformer model."""
        if self._encoder is None:
            if not SENTENCE_TRANSFORMERS_AVAILABLE:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                )
            
            logger.info("loading_encoder", f"Loading encoder: {self.model_name}", {"model": self.model_name})
            self._encoder = SentenceTransformer(self.model_name)
            embedding_dim = self._encoder.get_sentence_embedding_dimension()
            logger.info("encoder_loaded", f"Encoder loaded: {self.model_name}", {
                "model": self.model_name,
                "dimension": embedding_dim
            })
        
        return self._encoder
    
    def encode_text(self, text: str) -> np.ndarray:
        """
        Encode text into dense embedding.
        
        Args:
            text: Text to encode
            
        Returns:
            Embedding vector (1, D)
        """
        encoder = self._get_encoder()
        embedding = encoder.encode([text], convert_to_numpy=True)
        return embedding.astype(np.float32)
    
    def encode_product(self, product: Dict[str, Any]) -> np.ndarray:
        """
        Encode a product into dense embedding.
        
        Builds text representation from product fields:
        - name, description, category, brand
        - metadata (type-specific attributes)
        
        Args:
            product: Product dict with name, description, category, brand, metadata
            
        Returns:
            Embedding vector (1, D)
        """
        # Build text representation
        parts = []
        
        # Core fields
        if product.get("name"):
            parts.append(str(product["name"]))
        if product.get("description"):
            parts.append(str(product["description"]))
        if product.get("category"):
            parts.append(f"category: {product['category']}")
        if product.get("brand"):
            parts.append(f"brand: {product['brand']}")
        
        # Type-specific metadata
        metadata = product.get("metadata", {})
        if metadata:
            # Add relevant metadata fields
            for key, value in metadata.items():
                if value and isinstance(value, (str, int, float)):
                    parts.append(f"{key}: {value}")
        
        # Product type
        product_type = product.get("product_type", "")
        if product_type:
            parts.append(f"product type: {product_type}")
        
        text = " ".join(parts)
        
        # Check cache
        product_id = product.get("product_id", "")
        if self.use_cache and product_id in self._product_embeddings_cache:
            return self._product_embeddings_cache[product_id]
        
        # Encode
        embedding = self.encode_text(text)
        
        # Cache
        if self.use_cache and product_id:
            self._product_embeddings_cache[product_id] = embedding
        
        return embedding
    
    def build_index(
        self,
        products: List[Dict[str, Any]],
        save_index: bool = True
    ) -> None:
        """
        Build FAISS index from products.
        
        Args:
            products: List of product dicts
            save_index: Whether to save index to disk
        """
        if not FAISS_AVAILABLE:
            raise ImportError(
                "faiss-cpu not installed. "
                "Run: pip install faiss-cpu"
            )
        
        if not products:
            logger.warning("build_index_empty", "No products provided for index building", {})
            return
        
        logger.info("building_index", f"Building index for {len(products)} products", {"product_count": len(products)})
        
        encoder = self._get_encoder()
        embedding_dim = encoder.get_sentence_embedding_dimension()
        
        # Collect product texts and IDs for batch encoding
        product_texts = []
        product_ids = []
        
        for product in products:
            product_id = product.get("product_id")
            if not product_id:
                continue
            
            # Build text representation (same logic as encode_product but without encoding)
            parts = []
            if product.get("name"):
                parts.append(str(product["name"]))
            if product.get("description"):
                parts.append(str(product["description"]))
            if product.get("category"):
                parts.append(f"category: {product['category']}")
            if product.get("brand"):
                parts.append(f"brand: {product['brand']}")
            
            metadata = product.get("metadata", {})
            if metadata:
                for key, value in metadata.items():
                    if value and isinstance(value, (str, int, float)):
                        parts.append(f"{key}: {value}")
            
            product_type = product.get("product_type", "")
            if product_type:
                parts.append(f"product type: {product_type}")
            
            text = " ".join(parts)
            product_texts.append(text)
            product_ids.append(product_id)
        
        if not product_texts:
            logger.warning("build_index_no_products", "No valid products found for indexing", {})
            return
        
        # Batch encode all products at once (much faster than one-by-one)
        logger.info("batch_encoding", f"Batch encoding {len(product_texts)} products", {"batch_size": len(product_texts)})
        try:
            embeddings = encoder.encode(product_texts, convert_to_numpy=True, batch_size=32, show_progress_bar=False)
            embeddings = embeddings.astype(np.float32)
            logger.info("batch_encoding_complete", f"Encoded {len(embeddings)} products", {"embeddings_shape": embeddings.shape})
        except Exception as e:
            logger.error("batch_encoding_failed", f"Batch encoding failed: {e}", {"error": str(e)})
            raise
        
        # Cache embeddings
        if self.use_cache:
            for product_id, embedding in zip(product_ids, embeddings):
                self._product_embeddings_cache[product_id] = embedding.reshape(1, -1)
        
        # Create FAISS index
        logger.info("creating_faiss_index", "Creating FAISS index", {"index_type": self.index_type, "embedding_dim": embedding_dim})
        try:
            if self.index_type.lower() == "flat":
                index = faiss.IndexFlatL2(embedding_dim)
            else:
                # IVF index (for large datasets)
                nlist = min(100, len(embeddings) // 10)
                quantizer = faiss.IndexFlatL2(embedding_dim)
                index = faiss.IndexIVFFlat(quantizer, embedding_dim, nlist)
                index.train(embeddings)
            
            # Add embeddings to index
            logger.info("adding_to_index", f"Adding {len(embeddings)} embeddings to index", {})
            index.add(embeddings)
            logger.info("index_added", "Embeddings added to index", {"index_size": index.ntotal})
        except Exception as e:
            logger.error("faiss_index_failed", f"FAISS index creation failed: {e}", {"error": str(e)})
            raise
        
        # Store
        self._index = index
        self._product_ids = product_ids
        self._product_id_to_idx = {
            pid: idx for idx, pid in enumerate(product_ids)
        }
        
        logger.info("index_built", f"Index built: {len(product_ids)} products", {
            "product_count": len(product_ids),
            "index_size": index.ntotal,
            "dimension": embedding_dim
        })
        
        # Save to disk if requested
        if save_index:
            self._save_index()
    
    def _save_index(self):
        """Save index to disk for future use."""
        if self._index is None or not self._product_ids:
            return
        
        model_slug = self.model_name.replace("/", "_").replace("-", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        index_path = self.index_dir / f"mcp_index_{model_slug}_{timestamp}.index"
        ids_path = self.index_dir / f"mcp_ids_{model_slug}_{timestamp}.pkl"
        
        try:
            faiss.write_index(self._index, str(index_path))
            with open(ids_path, 'wb') as f:
                pickle.dump(self._product_ids, f)
            
            logger.info("index_saved", f"Index saved to {index_path}", {
                "index_path": str(index_path),
                "ids_path": str(ids_path)
            })
        except Exception as e:
            logger.error("save_index_failed", f"Failed to save index: {e}", {"error": str(e)})
    
    def _load_index(self, index_path: Optional[Path] = None):
        """Load pre-computed index from disk."""
        if not FAISS_AVAILABLE:
            return False
        
        if index_path is None:
            # Find latest index
            model_slug = self.model_name.replace("/", "_").replace("-", "_")
            index_files = list(self.index_dir.glob(f"mcp_index_{model_slug}_*.index"))
            if not index_files:
                return False
            index_path = max(index_files, key=lambda p: p.stat().st_mtime)
        
        # Find corresponding IDs file
        ids_path = index_path.with_suffix('.pkl').with_name(
            index_path.stem.replace('index', 'ids')
        )
        
        if not ids_path.exists():
            return False
        
        try:
            self._index = faiss.read_index(str(index_path))
            with open(ids_path, 'rb') as f:
                self._product_ids = pickle.load(f)
            
            self._product_id_to_idx = {
                pid: idx for idx, pid in enumerate(self._product_ids)
            }
            
            logger.info("index_loaded", f"Index loaded from {index_path}", {
                "index_path": str(index_path),
                "product_count": len(self._product_ids)
            })
            return True
        except Exception as e:
            logger.error("load_index_failed", f"Failed to load index: {e}", {"error": str(e)})
            return False
    
    def search(
        self,
        query: str,
        k: int = 20,
        product_ids: Optional[List[str]] = None
    ) -> Tuple[List[str], List[float]]:
        """
        Search for similar products using vector similarity.
        
        Args:
            query: Natural language query
            k: Number of results to return
            product_ids: Optional list of candidate product IDs to search within
            
        Returns:
            Tuple of (product_ids, similarity_scores)
        """
        if not self._index:
            # Try to load existing index
            if not self._load_index():
                logger.warning("search_no_index", "No index available, returning empty results", {})
                return [], []
        
        # Encode query
        query_embedding = self.encode_text(query)
        
        # Search
        if product_ids:
            # Search within subset
            return self._search_within_candidates(query_embedding, product_ids, k)
        else:
            # Search entire index
            distances, indices = self._index.search(query_embedding, k)
            similarities = 1.0 / (1.0 + distances[0])
            result_ids = [self._product_ids[idx] for idx in indices[0]]
            result_scores = similarities.tolist()
            
            logger.info("vector_search", f"Vector search: {len(result_ids)} results", {
                "query": query[:100],
                "results_count": len(result_ids),
                "top_score": float(result_scores[0]) if result_scores else 0.0
            })
            
            return result_ids, result_scores
    
    def _search_within_candidates(
        self,
        query_embedding: np.ndarray,
        candidate_ids: List[str],
        k: int
    ) -> Tuple[List[str], List[float]]:
        """Search within a subset of candidate products."""
        valid_ids = [
            pid for pid in candidate_ids
            if pid in self._product_id_to_idx
        ]
        
        if not valid_ids:
            return [], []
        
        # Compute similarities for candidates
        similarities = []
        for product_id in valid_ids:
            idx = self._product_id_to_idx[product_id]
            candidate_embedding = self._index.reconstruct(int(idx))
            distance = np.linalg.norm(query_embedding[0] - candidate_embedding)
            similarity = 1.0 / (1.0 + distance)
            similarities.append(similarity)
        
        # Sort by similarity
        sorted_pairs = sorted(
            zip(valid_ids, similarities),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Take top k
        if k:
            sorted_pairs = sorted_pairs[:k]
        
        result_ids = [pid for pid, _ in sorted_pairs]
        result_scores = [score for _, score in sorted_pairs]
        
        return result_ids, result_scores
    
    def rank_products(
        self,
        products: List[Dict[str, Any]],
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Rank products by semantic similarity to query.
        
        Args:
            products: List of product dicts
            query: Natural language query
            
        Returns:
            List of products ranked by similarity (with _vector_score added)
        """
        if not products:
            return products
        
        # Encode query
        query_embedding = self.encode_text(query)
        
        # Compute similarities
        scored_products = []
        for product in products:
            product_embedding = self.encode_product(product)
            distance = np.linalg.norm(query_embedding[0] - product_embedding[0])
            similarity = 1.0 / (1.0 + distance)
            
            product_copy = product.copy()
            product_copy["_vector_score"] = float(similarity)
            scored_products.append(product_copy)
        
        # Sort by similarity
        scored_products.sort(key=lambda p: p["_vector_score"], reverse=True)
        
        logger.info("products_ranked", f"Ranked {len(scored_products)} products", {
            "query": query[:100],
            "product_count": len(scored_products),
            "top_score": scored_products[0]["_vector_score"] if scored_products else 0.0
        })
        
        return scored_products


# Global instance (lazy-loaded)
_vector_store: Optional[UniversalEmbeddingStore] = None


def get_vector_store() -> UniversalEmbeddingStore:
    """Get or create global vector store instance."""
    global _vector_store
    
    if _vector_store is None:
        _vector_store = UniversalEmbeddingStore()
    
    return _vector_store
