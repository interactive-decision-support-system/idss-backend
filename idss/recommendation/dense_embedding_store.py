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
        index_type: str = "Flat"
    ):
        """
        Initialize the dense embedding store.

        Args:
            index_dir: Directory containing FAISS index files
            model_name: Embedding model name
            version: Embedding version
            index_type: Index type (Flat or IVF)
        """
        self.model_name = model_name
        self.version = version
        self.index_type = index_type

        # Set default paths
        if index_dir is None:
            index_dir = _project_root() / "data" / "car_dataset_idss" / "faiss_indices"
        self.index_dir = index_dir

        # Load FAISS index and VIN mapping
        self.index = None
        self.vins = None
        self.vin_to_idx = None
        self._load_index()

        # Load sentence transformer model (lazy loaded)
        self._encoder = None

    def _load_index(self):
        """Load FAISS index, VIN mapping, and metadata from disk."""
        try:
            import faiss
        except ImportError:
            logger.error("faiss-cpu not installed. Please run: pip install faiss-cpu")
            raise

        # Sanitize model name for filename
        model_slug = self.model_name.replace("/", "_").replace("-", "_")

        # Build file paths
        index_path = self.index_dir / f"faiss_{self.index_type.lower()}_{model_slug}_{self.version}.index"
        vins_path = self.index_dir / f"vins_{self.index_type.lower()}_{model_slug}_{self.version}.pkl"
        metadata_path = self.index_dir / f"metadata_{self.index_type.lower()}_{model_slug}_{self.version}.pkl"

        # Check if files exist
        if not index_path.exists():
            logger.error(f"FAISS index not found at {index_path}")
            raise FileNotFoundError(f"FAISS index not found at {index_path}")

        # Load FAISS index
        logger.info(f"Loading FAISS index from {index_path}")
        self.index = faiss.read_index(str(index_path))
        logger.info(f"Loaded index with {self.index.ntotal:,} vectors")

        # Load VIN mapping
        logger.info(f"Loading VIN mapping from {vins_path}")
        with open(vins_path, 'rb') as f:
            self.vins = pickle.load(f)
        logger.info(f"Loaded {len(self.vins):,} VINs")

        # Create reverse mapping (VIN -> index)
        self.vin_to_idx = {vin: idx for idx, vin in enumerate(self.vins)}

        # Load metadata
        if metadata_path.exists():
            with open(metadata_path, 'rb') as f:
                metadata = pickle.load(f)
                logger.info(f"Index metadata: {metadata}")

    def _get_encoder(self):
        """Lazy load the sentence transformer model."""
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                logger.error("sentence-transformers not installed")
                raise

            logger.info(f"Loading sentence transformer model: {self.model_name}")
            self._encoder = SentenceTransformer(self.model_name)
            logger.info(f"Model loaded (dimension: {self._encoder.get_sentence_embedding_dimension()})")

        return self._encoder

    def encode_text(self, text: str) -> np.ndarray:
        """Encode text into dense embedding."""
        encoder = self._get_encoder()
        embedding = encoder.encode([text], convert_to_numpy=True)
        return embedding.astype(np.float32)

    def encode_features(self, features: List[str]) -> np.ndarray:
        """Encode features using sum-of-features method."""
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
        """Search for similar vehicles using dense embedding similarity."""
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
        if self.index is None:
            raise RuntimeError("FAISS index not loaded")

        valid_vins = [vin for vin in candidate_vins if vin in self.vin_to_idx]
        if not valid_vins:
            return [], []

        candidate_indices = [self.vin_to_idx[vin] for vin in valid_vins]

        if method == "sum" and isinstance(query_input, list):
            query_embedding = self.encode_features(query_input)
        else:
            if isinstance(query_input, list):
                query_input = " ".join(query_input)
            query_embedding = self.encode_text(query_input)

        similarities = []
        for idx in candidate_indices:
            candidate_embedding = self.index.reconstruct(int(idx))
            distance = np.linalg.norm(query_embedding[0] - candidate_embedding)
            similarity = 1.0 / (1.0 + distance)
            similarities.append(similarity)

        sorted_pairs = sorted(zip(valid_vins, similarities), key=lambda x: x[1], reverse=True)

        if k is not None:
            sorted_pairs = sorted_pairs[:k]

        result_vins = [vin for vin, _ in sorted_pairs]
        result_scores = [score for _, score in sorted_pairs]

        return result_vins, result_scores

    def get_embedding_for_vin(self, vin: str) -> Optional[np.ndarray]:
        """Get the precomputed embedding for a specific VIN."""
        if vin not in self.vin_to_idx:
            return None
        idx = self.vin_to_idx[vin]
        embedding = self.index.reconstruct(int(idx))
        return embedding
