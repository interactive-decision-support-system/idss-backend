"""
Dense Embedding Store: Manages FAISS index for fast similarity search.

This class loads precomputed embeddings and FAISS index, and provides
efficient similarity search over all vehicles.
"""
import pickle
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import numpy as np

from idss.utils.logger import get_logger

logger = get_logger("recommendation.dense_embedding_store")


def _project_root() -> Path:
    """Return project root."""
    return Path(__file__).resolve().parent.parent.parent


class DenseEmbeddingStore:
    """
    Manages FAISS index and provides fast dense embedding similarity search.

    Usage:
        store = DenseEmbeddingStore()
        query_text = "luxury SUV with leather seats"
        vins, scores = store.search(query_text, k=20)
    """

    def __init__(
        self,
        index_dir: Optional[Path] = None,
        model_name: str = "all-mpnet-base-v2",
        version: str = "v1",
        index_type: str = "Flat",
        use_supabase: bool = True,
        preload_model: bool = False
    ):
        """
        Initialize the dense embedding store.
        """
        self.model_name = model_name
        self.version = version
        self.index_type = index_type
        self.use_supabase = use_supabase

        # Set default paths
        if index_dir is None:
            index_dir = _project_root() / "data" / "car_dataset_idss" / "faiss_indices"
        self.index_dir = index_dir

        # Load FAISS index and VIN mapping (only if not using Supabase)
        self.index = None
        self.vins = None
        self.vin_to_idx = None
        
        if not self.use_supabase:
            self._load_index()
        else:
            from idss.utils.supabase_client import supabase
            self.supabase = supabase
            logger.info("Using Supabase for dense embeddings")

        # Load sentence transformer model (lazy loaded)
        self._encoder = None
        
        # Cache for embeddings
        self._embedding_cache = {}

        if preload_model:
            self._get_encoder()

    def _load_index(self):
        # ... (Existing local FAISS loading logic)
        try:
            import faiss
        except ImportError:
            logger.error("faiss-cpu not installed. Please run: pip install faiss-cpu")
            raise

        model_slug = self.model_name.replace("/", "_").replace("-", "_")
        index_path = self.index_dir / f"faiss_{self.index_type.lower()}_{model_slug}_{self.version}.index"
        vins_path = self.index_dir / f"vins_{self.index_type.lower()}_{model_slug}_{self.version}.pkl"

        if not index_path.exists():
            logger.error(f"FAISS index not found at {index_path}")
            raise FileNotFoundError(f"FAISS index not found at {index_path}")

        self.index = faiss.read_index(str(index_path))
        with open(vins_path, 'rb') as f:
            self.vins = pickle.load(f)
        self.vin_to_idx = {vin: idx for idx, vin in enumerate(self.vins)}

    def _get_encoder(self):
        """Lazy load the sentence transformer model."""
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                logger.error("sentence-transformers not installed")
                raise
            self._encoder = SentenceTransformer(self.model_name)
        return self._encoder

    def encode_text(self, text: str) -> np.ndarray:
        encoder = self._get_encoder()
        embedding = encoder.encode([text], convert_to_numpy=True)
        return embedding.astype(np.float32)

    def encode_features(self, features: List[str]) -> np.ndarray:
        if not features:
            encoder = self._get_encoder()
            embedding_dim = encoder.get_sentence_embedding_dimension()
            return np.zeros((1, embedding_dim), dtype=np.float32)

        encoder = self._get_encoder()
        feature_embeddings = encoder.encode(features, convert_to_numpy=True)
        summed = np.sum(feature_embeddings, axis=0)
        normalized = summed / (np.linalg.norm(summed) + 1e-8)
        return normalized.reshape(1, -1).astype(np.float32)

    def search(self, query_text: str, k: int = 20) -> Tuple[List[str], List[float]]:
        """Search for similar vehicles."""
        if self.use_supabase:
            # Full semantic search via pgvector on Supabase would be ideal
            # But here we probably fetch candidate VINs first from SQL
            # or perform a vector search if the table supports it.
            # For now, we'll assume we fetch then rank.
            logger.warning("Global semantic search on Supabase not yet implemented. Use search_by_vins.")
            return [], []

        if self.index is None:
            raise RuntimeError("FAISS index not loaded")

        query_embedding = self.encode_text(query_text)
        distances, indices = self.index.search(query_embedding, k)
        similarities = 1.0 / (1.0 + distances[0])
        result_vins = [self.vins[idx] for idx in indices[0]]
        result_scores = similarities.tolist()
        return result_vins, result_scores

    def search_by_vins(
        self,
        candidate_vins: List[str],
        query_input,
        k: Optional[int] = None,
        method: str = "sum"
    ) -> Tuple[List[str], List[float]]:
        """Search within a subset of candidate vehicles."""
        if not candidate_vins:
            return [], []

        if method == "sum" and isinstance(query_input, list):
            query_embedding = self.encode_features(query_input)
        else:
            if isinstance(query_input, list):
                query_input = " ".join(query_input)
            query_embedding = self.encode_text(query_input)

        # Fetch embeddings for candidates
        similarities = []
        for vin in candidate_vins:
            candidate_embedding = self.get_embedding_for_vin(vin)
            if candidate_embedding is not None:
                # Cosine similarity (embeddings are typically normalized)
                dot_product = np.dot(query_embedding[0], candidate_embedding)
                similarities.append(float(dot_product))
            else:
                similarities.append(0.0)

        sorted_pairs = sorted(zip(candidate_vins, similarities), key=lambda x: x[1], reverse=True)
        if k is not None:
            sorted_pairs = sorted_pairs[:k]

        return [vin for vin, _ in sorted_pairs], [score for _, score in sorted_pairs]

    def get_embedding_for_vin(self, vin: str) -> Optional[np.ndarray]:
        """Get precomputed embedding for a VIN."""
        # 1. Check cache
        if vin in self._embedding_cache:
            return self._embedding_cache[vin]

        # 2. Check Supabase
        if self.use_supabase:
            try:
                # Fetch from 'vehicle_embeddings' table
                # Expected schema: vin (text), embedding (vector/json)
                res = self.supabase.select("vehicle_embeddings", filters={"vin": vin}, limit=1)
                if res and "embedding" in res[0]:
                    raw_emb = res[0]["embedding"]
                    # Supabase returns embeddings as stringified JSON arrays
                    if isinstance(raw_emb, str):
                        import json
                        raw_emb = json.loads(raw_emb)
                    emb = np.array(raw_emb, dtype=np.float32)
                    self._embedding_cache[vin] = emb
                    return emb
            except Exception as e:
                logger.error(f"Failed to fetch embedding for {vin} from Supabase: {e}")
            return None

        # 3. Check local FAISS
        if self.index is not None and vin in self.vin_to_idx:
            idx = self.vin_to_idx[vin]
            emb = self.index.reconstruct(int(idx))
            self._embedding_cache[vin] = emb
            return emb
            
        return None
