"""
Universal Vector Search for MCP - All Product Types.

Reuses IDSS embedding code but adapts it for universal product search.
Supports: vehicles, e-commerce, real estate, travel, and any future product types.

Storage layout is per-(merchant_id, strategy):
    data/merchants/<merchant_id>/<strategy>/faiss.bin   # FAISS index
    data/merchants/<merchant_id>/<strategy>/ids.pkl     # id-map sidecar

One MerchantAgent instance corresponds to one (merchant_id, strategy) pair, and
each pair owns its own FAISS index — no cross-merchant bleed at retrieval time.
"""

import pickle
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Tuple as _Tuple
import numpy as np

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


# Repository-root-relative base directory for per-merchant vector indices.
# Resolves to <mcp-server>/data/merchants/.
_MERCHANT_DATA_ROOT = Path(__file__).parent.parent / "data" / "merchants"


def merchant_index_dir(merchant_id: str, strategy: str) -> Path:
    """Return the directory that owns the FAISS index for this (merchant, strategy)."""
    return _MERCHANT_DATA_ROOT / merchant_id / strategy


def merchant_index_path(merchant_id: str, strategy: str) -> Path:
    """Absolute path to the FAISS index binary for this (merchant, strategy)."""
    return merchant_index_dir(merchant_id, strategy) / "faiss.bin"


def merchant_ids_path(merchant_id: str, strategy: str) -> Path:
    """Absolute path to the id-map sidecar for this (merchant, strategy)."""
    return merchant_index_dir(merchant_id, strategy) / "ids.pkl"


class UniversalEmbeddingStore:
    """
    Per-(merchant_id, strategy) embedding store.

    Usage:
        store = UniversalEmbeddingStore(merchant_id="acme", strategy="normalizer_v1")
        store.build_index(products)
        product_ids, scores = store.search("laptop for video editing", k=10)
    """

    def __init__(
        self,
        merchant_id: str,
        strategy: str,
        model_name: str = "all-mpnet-base-v2",
        index_type: str = "Flat",
        use_cache: bool = True,
    ):
        """
        Args:
            merchant_id: Merchant slug. Scopes the index to this merchant's catalog.
            strategy:    Enrichment strategy label (e.g. ``normalizer_v1``). A
                         merchant can run multiple strategies side-by-side; each
                         gets its own KG and vector index.
            model_name:  Sentence transformer model name.
            index_type:  FAISS index type (Flat or IVF).
            use_cache:   Whether to load/save the index from disk on init.
        """
        self.merchant_id = merchant_id
        self.strategy = strategy
        self.model_name = model_name
        self.index_type = index_type
        self.use_cache = use_cache

        # Lazy-loaded components
        self._encoder = None
        self._index = None
        self._product_ids: List[str] = []
        self._product_id_to_idx: Dict[str, int] = {}
        self._product_embeddings_cache: Dict[str, np.ndarray] = {}  # product_id -> embedding

        # Per-merchant index directory
        self.index_dir = merchant_index_dir(merchant_id, strategy)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = merchant_index_path(merchant_id, strategy)
        self.ids_path = merchant_ids_path(merchant_id, strategy)

        logger.info("vector_store_init", "Initializing vector store", {
            "merchant_id": merchant_id,
            "strategy": strategy,
            "model_name": model_name,
            "index_type": index_type,
            "use_cache": use_cache,
            "index_dir": str(self.index_dir),
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
        """Encode text into a (1, D) embedding."""
        encoder = self._get_encoder()
        embedding = encoder.encode([text], convert_to_numpy=True)
        return embedding.astype(np.float32)

    def encode_product(self, product: Dict[str, Any]) -> np.ndarray:
        """Encode a product into a (1, D) embedding."""
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

        product_id = product.get("product_id", "")
        if self.use_cache and product_id in self._product_embeddings_cache:
            return self._product_embeddings_cache[product_id]

        embedding = self.encode_text(text)

        if self.use_cache and product_id:
            self._product_embeddings_cache[product_id] = embedding

        return embedding

    def build_index(
        self,
        products: List[Dict[str, Any]],
        save_index: bool = True
    ) -> None:
        """Build FAISS index from products and optionally persist to disk."""
        if not FAISS_AVAILABLE:
            raise ImportError(
                "faiss-cpu not installed. Run: pip install faiss-cpu"
            )

        if not products:
            logger.warning("build_index_empty", "No products provided for index building", {
                "merchant_id": self.merchant_id, "strategy": self.strategy,
            })
            return

        logger.info("building_index", f"Building index for {len(products)} products", {
            "merchant_id": self.merchant_id,
            "strategy": self.strategy,
            "product_count": len(products),
        })

        encoder = self._get_encoder()
        embedding_dim = encoder.get_sentence_embedding_dimension()

        product_texts = []
        product_ids = []

        for product in products:
            product_id = product.get("product_id")
            if not product_id:
                continue

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

        logger.info("batch_encoding", f"Batch encoding {len(product_texts)} products", {"batch_size": len(product_texts)})
        try:
            embeddings = encoder.encode(product_texts, convert_to_numpy=True, batch_size=32, show_progress_bar=False)
            embeddings = embeddings.astype(np.float32)
            logger.info("batch_encoding_complete", f"Encoded {len(embeddings)} products", {"embeddings_shape": embeddings.shape})
        except Exception as e:
            logger.error("batch_encoding_failed", f"Batch encoding failed: {e}", {"error": str(e)})
            raise

        if self.use_cache:
            for product_id, embedding in zip(product_ids, embeddings):
                self._product_embeddings_cache[product_id] = embedding.reshape(1, -1)

        logger.info("creating_faiss_index", "Creating FAISS index", {"index_type": self.index_type, "embedding_dim": embedding_dim})
        try:
            if self.index_type.lower() == "flat":
                index = faiss.IndexFlatL2(embedding_dim)
            else:
                nlist = min(100, len(embeddings) // 10)
                quantizer = faiss.IndexFlatL2(embedding_dim)
                index = faiss.IndexIVFFlat(quantizer, embedding_dim, nlist)
                index.train(embeddings)

            index.add(embeddings)
            logger.info("index_added", "Embeddings added to index", {"index_size": index.ntotal})
        except Exception as e:
            logger.error("faiss_index_failed", f"FAISS index creation failed: {e}", {"error": str(e)})
            raise

        self._index = index
        self._product_ids = product_ids
        self._product_id_to_idx = {
            pid: idx for idx, pid in enumerate(product_ids)
        }

        logger.info("index_built", f"Index built: {len(product_ids)} products", {
            "merchant_id": self.merchant_id,
            "strategy": self.strategy,
            "product_count": len(product_ids),
            "index_size": index.ntotal,
            "dimension": embedding_dim,
        })

        if save_index:
            self._save_index()

    def _save_index(self):
        """Save index + id-map to the merchant's own directory."""
        if self._index is None or not self._product_ids:
            return

        try:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self._index, str(self.index_path))
            with open(self.ids_path, 'wb') as f:
                pickle.dump(self._product_ids, f)

            logger.info("index_saved", f"Index saved to {self.index_path}", {
                "merchant_id": self.merchant_id,
                "strategy": self.strategy,
                "index_path": str(self.index_path),
                "ids_path": str(self.ids_path),
            })
        except Exception as e:
            logger.error("save_index_failed", f"Failed to save index: {e}", {"error": str(e)})

    def _load_index(self) -> bool:
        """Load the merchant's FAISS index and id-map from disk, if present."""
        if not FAISS_AVAILABLE:
            return False

        if not self.index_path.exists() or not self.ids_path.exists():
            return False

        try:
            self._index = faiss.read_index(str(self.index_path))
            with open(self.ids_path, 'rb') as f:
                self._product_ids = pickle.load(f)

            self._product_id_to_idx = {
                pid: idx for idx, pid in enumerate(self._product_ids)
            }

            logger.info("index_loaded", f"Index loaded from {self.index_path}", {
                "merchant_id": self.merchant_id,
                "strategy": self.strategy,
                "index_path": str(self.index_path),
                "product_count": len(self._product_ids),
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
        """Vector search against this merchant's index."""
        if not self._index:
            if not self._load_index():
                logger.warning("search_no_index", "No index available, returning empty results", {
                    "merchant_id": self.merchant_id, "strategy": self.strategy,
                })
                return [], []

        query_embedding = self.encode_text(query)

        if product_ids:
            return self._search_within_candidates(query_embedding, product_ids, k)

        distances, indices = self._index.search(query_embedding, k)
        similarities = 1.0 / (1.0 + distances[0])
        result_ids = [self._product_ids[idx] for idx in indices[0]]
        result_scores = similarities.tolist()

        logger.info("vector_search", f"Vector search: {len(result_ids)} results", {
            "merchant_id": self.merchant_id,
            "strategy": self.strategy,
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

        similarities = []
        for product_id in valid_ids:
            idx = self._product_id_to_idx[product_id]
            candidate_embedding = self._index.reconstruct(int(idx))
            distance = np.linalg.norm(query_embedding[0] - candidate_embedding)
            similarity = 1.0 / (1.0 + distance)
            similarities.append(similarity)

        sorted_pairs = sorted(
            zip(valid_ids, similarities),
            key=lambda x: x[1],
            reverse=True
        )

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
        """Rank products by semantic similarity to query."""
        if not products:
            return products

        query_embedding = self.encode_text(query)

        scored_products = []
        for product in products:
            product_embedding = self.encode_product(product)
            distance = np.linalg.norm(query_embedding[0] - product_embedding[0])
            similarity = 1.0 / (1.0 + distance)

            product_copy = product.copy()
            product_copy["_vector_score"] = float(similarity)
            scored_products.append(product_copy)

        scored_products.sort(key=lambda p: p["_vector_score"], reverse=True)

        logger.info("products_ranked", f"Ranked {len(scored_products)} products", {
            "query": query[:100],
            "product_count": len(scored_products),
            "top_score": scored_products[0]["_vector_score"] if scored_products else 0.0
        })

        return scored_products


# Per-(merchant_id, strategy) store cache. Each pair gets its own FAISS index
# and its own id-map; callers that ask twice for the same pair share state.
_vector_stores: Dict[_Tuple[str, str], UniversalEmbeddingStore] = {}

DEFAULT_STRATEGY = "normalizer_v1"


def get_vector_store(
    merchant_id: str,
    strategy: str = DEFAULT_STRATEGY,
) -> UniversalEmbeddingStore:
    """Return (and cache) the vector store for a (merchant_id, strategy) pair.

    Idempotent: repeat calls with the same pair return the same instance, so
    the encoder and loaded index are reused.
    """
    key = (merchant_id, strategy)
    store = _vector_stores.get(key)
    if store is None:
        store = UniversalEmbeddingStore(merchant_id=merchant_id, strategy=strategy)
        _vector_stores[key] = store
    return store


def reset_vector_store_cache() -> None:
    """Drop all cached stores. Used by tests that need a clean slate."""
    _vector_stores.clear()
