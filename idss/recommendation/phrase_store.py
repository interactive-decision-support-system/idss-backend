"""
Phrase Store: Pre-computed INDIVIDUAL phrase embeddings for Method 3.

DESIGN (from METHOD3_DESIGN.md):
- Each vehicle has multiple pros phrases and cons phrases
- Each phrase is embedded SEPARATELY (not concatenated!)
- Pos_j(v) = Σ_{k: pros} max(0, cosine(preference_j, phrase_k))
- Sum across individual phrase similarities

EFFICIENCY STRATEGY:
- Pre-compute embeddings for ALL individual phrases at startup
- Store as lists of embeddings per MMY
- Impute missing MMYs from most recent same make+model
- Fast inference via vectorized numpy operations
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from idss.utils.logger import get_logger

logger = get_logger("recommendation.phrase_store")


@dataclass
class VehiclePhrases:
    """Pre-computed individual phrase embeddings for a vehicle."""
    make: str
    model: str
    year: int

    # Individual phrase texts
    pros_phrases: List[str]  # e.g., ["high fuel economy", "spacious cabin", ...]
    cons_phrases: List[str]  # e.g., ["underpowered engine", "dated interior", ...]

    # Individual phrase embeddings (one per phrase)
    pros_embeddings: np.ndarray  # Shape (N_pros, D) where D=768
    cons_embeddings: np.ndarray  # Shape (N_cons, D)

    imputed: bool = False  # Whether this was imputed from another year


class PhraseStore:
    """
    Pre-computed INDIVIDUAL phrase embedding store with 100% MMY coverage.

    Design Philosophy (per METHOD3_DESIGN.md):
    - Each pros/cons phrase is embedded SEPARATELY
    - Scoring sums similarities across individual phrases
    - Pay upfront cost at initialization (~60-120s)
    - Get fast inference via pre-computed phrase embeddings

    Usage:
        store = PhraseStore()  # Takes 60-120s, loads ALL phrase embeddings
        phrases = store.get_phrases("Toyota", "Camry", 2023)
        # phrases.pros_embeddings shape: (N_pros, 768)
        # phrases.cons_embeddings shape: (N_cons, 768)
    """

    def __init__(
        self,
        reviews_db_path: Optional[Path] = None,
        vehicles_db_path: Optional[Path] = None,
        embeddings_dir: Optional[Path] = None,
        model_name: str = "all-mpnet-base-v2",
        preload: bool = True
    ):
        """
        Initialize phrase store with individual phrase embeddings.

        Args:
            reviews_db_path: Path to Tavily reviews database (only if building from scratch)
            vehicles_db_path: Path to unified vehicle listings database
            embeddings_dir: Path to pre-computed embeddings directory (default: data/car_dataset_idss/phrase_embeddings)
            model_name: Sentence transformer model name (only if building from scratch)
            preload: If True, load all embeddings at init
        """
        if reviews_db_path is None:
            reviews_db_path = Path("data/car_dataset_idss/vehicle_reviews_tavily.db")
        if vehicles_db_path is None:
            vehicles_db_path = Path("data/car_dataset_idss/uni_vehicles.db")
        if embeddings_dir is None:
            embeddings_dir = Path("data/car_dataset_idss/phrase_embeddings")

        self.reviews_db_path = reviews_db_path
        self.vehicles_db_path = vehicles_db_path
        self.embeddings_dir = embeddings_dir
        self.model_name = model_name

        # Lazy-loaded encoder (only needed for encoding new user preferences)
        self._encoder = None

        # Pre-computed storage: (MAKE_UPPER, MODEL_UPPER, year) -> VehiclePhrases
        self._phrases_by_mmy: Dict[Tuple[str, str, int], VehiclePhrases] = {}

        # Pre-load all embeddings with imputation
        if preload:
            # Try to load from pre-computed files first
            if self._load_precomputed_embeddings():
                logger.info(f"Loaded pre-computed embeddings from {self.embeddings_dir}")
            else:
                logger.warning(f"Pre-computed embeddings not found, building from scratch...")
                # Fall back to computing on-the-fly
                if not self.reviews_db_path.exists():
                    logger.error(f"Reviews database not found: {self.reviews_db_path}")
                    raise FileNotFoundError(f"Database not found: {self.reviews_db_path}")

                self._preload_with_imputation()

        logger.info(f"PhraseStore ready: {len(self._phrases_by_mmy)} MMYs loaded")

    def _load_precomputed_embeddings(self) -> bool:
        """
        Load pre-computed phrase embeddings from disk.

        Returns:
            True if successfully loaded, False if files not found
        """
        import pickle

        embeddings_path = self.embeddings_dir / "phrase_embeddings.npy"
        index_path = self.embeddings_dir / "phrase_index.pkl"
        metadata_path = self.embeddings_dir / "metadata.json"

        # Check if all required files exist
        if not embeddings_path.exists() or not index_path.exists():
            logger.debug(f"Pre-computed embeddings not found at {self.embeddings_dir}")
            return False

        logger.info(f"Loading pre-computed embeddings from {self.embeddings_dir}...")

        # Load embeddings array
        logger.info("  Loading embeddings array...")
        all_embeddings = np.load(embeddings_path)
        logger.info(f"    Loaded {all_embeddings.shape[0]:,} phrase embeddings")

        # Load index
        logger.info("  Loading phrase index...")
        with open(index_path, "rb") as f:
            phrase_index = pickle.load(f)
        logger.info(f"    Loaded index for {len(phrase_index):,} MMYs")

        # Load phrase texts
        phrases_path = self.embeddings_dir / "phrase_texts.pkl"
        if phrases_path.exists():
            logger.info("  Loading phrase texts...")
            with open(phrases_path, "rb") as f:
                all_phrase_texts = pickle.load(f)
            logger.info(f"    Loaded {len(all_phrase_texts):,} phrase texts")
        else:
            logger.warning(f"  Phrase texts not found at {phrases_path}")
            logger.warning("  Will use empty phrase lists (scores will work, but no text display)")
            all_phrase_texts = None

        # Build VehiclePhrases objects from pre-computed embeddings
        logger.info("  Building VehiclePhrases objects...")
        reviews_by_mmy = {}
        reviews_by_mm = {}

        for item in phrase_index:
            make = item["make"]
            model = item["model"]
            year = item["year"]
            pros_start = item["pros_start"]
            n_pros = item["n_pros"]
            cons_start = item["cons_start"]
            n_cons = item["n_cons"]

            # Extract embeddings for this MMY
            pros_embeddings = all_embeddings[pros_start:pros_start + n_pros]
            cons_embeddings = all_embeddings[cons_start:cons_start + n_cons]

            # Extract phrase texts if available
            if all_phrase_texts is not None:
                pros_phrases = all_phrase_texts[pros_start:pros_start + n_pros]
                cons_phrases = all_phrase_texts[cons_start:cons_start + n_cons]
            else:
                pros_phrases = []
                cons_phrases = []

            vehicle_phrases = VehiclePhrases(
                make=make,
                model=model,
                year=year,
                pros_phrases=pros_phrases,
                cons_phrases=cons_phrases,
                pros_embeddings=pros_embeddings,
                cons_embeddings=cons_embeddings,
                imputed=False
            )

            # Store by exact MMY
            key_mmy = (make.upper(), model.upper(), year)
            reviews_by_mmy[key_mmy] = vehicle_phrases

            # Store by MM for imputation
            key_mm = (make.upper(), model.upper())
            if key_mm not in reviews_by_mm:
                reviews_by_mm[key_mm] = []
            reviews_by_mm[key_mm].append(vehicle_phrases)

        # Sort each MM list by year descending (prefer most recent)
        for key in reviews_by_mm:
            reviews_by_mm[key].sort(key=lambda x: x.year, reverse=True)

        logger.info(f"  Built {len(reviews_by_mmy):,} VehiclePhrases objects")

        # Step 2: Get all MMYs from vehicle listings and impute missing
        logger.info("  Loading all MMYs from vehicle listings...")
        all_mmys = self._get_all_vehicle_mmys()
        logger.info(f"    Found {len(all_mmys):,} unique MMYs in vehicle listings")

        # Build full coverage with imputation
        logger.info("  Building full coverage with imputation...")
        imputed_count = 0

        for make, model, year in all_mmys:
            key_mmy = (make.upper(), model.upper(), year)

            # If we have exact MMY, use it
            if key_mmy in reviews_by_mmy:
                self._phrases_by_mmy[key_mmy] = reviews_by_mmy[key_mmy]
                continue

            # Otherwise, impute from most recent same make+model
            key_mm = (make.upper(), model.upper())
            if key_mm in reviews_by_mm:
                # Find most recent review for this make+model
                source_phrases = reviews_by_mm[key_mm][0]  # Already sorted by year desc

                # Create imputed copy
                imputed_phrases = VehiclePhrases(
                    make=make,
                    model=model,
                    year=year,  # Use requested year, not source year
                    pros_phrases=source_phrases.pros_phrases.copy(),
                    cons_phrases=source_phrases.cons_phrases.copy(),
                    pros_embeddings=source_phrases.pros_embeddings.copy(),
                    cons_embeddings=source_phrases.cons_embeddings.copy(),
                    imputed=True
                )
                self._phrases_by_mmy[key_mmy] = imputed_phrases
                imputed_count += 1

        logger.info(f"    Imputed {imputed_count:,} MMYs from recent same make+model")
        logger.info(f"    Final coverage: {len(self._phrases_by_mmy):,}/{len(all_mmys):,} MMYs " +
                   f"({100*len(self._phrases_by_mmy)/len(all_mmys) if all_mmys else 0:.1f}%)")

        return True

    def _get_encoder(self):
        """Lazy load the sentence transformer model."""
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                logger.error("sentence-transformers not installed")
                logger.error("Please run: pip install sentence-transformers")
                raise

            logger.info(f"Loading sentence transformer: {self.model_name}")
            self._encoder = SentenceTransformer(self.model_name)
            logger.info(f"Model loaded (dim={self._encoder.get_sentence_embedding_dimension()})")

        return self._encoder

    def _preload_with_imputation(self) -> None:
        """
        Pre-compute individual phrase embeddings for ALL MMYs with imputation.

        Strategy:
        1. Load all MMYs with reviews from Tavily DB
        2. Batch-embed EACH individual phrase (not concatenated!)
        3. Get list of ALL MMYs from vehicle listings
        4. For missing MMYs: impute from most recent same make+model
        """
        logger.info("Pre-loading INDIVIDUAL phrase embeddings with imputation...")

        # Step 1: Load all reviews and batch-embed individual phrases
        logger.info("Step 1/3: Loading reviews and embedding individual phrases...")
        reviews_by_mmy, reviews_by_mm = self._load_and_embed_phrases()

        logger.info(f"  Loaded {len(reviews_by_mmy)} MMYs with phrase embeddings")

        # Step 2: Get all MMYs from vehicle listings
        logger.info("Step 2/3: Loading all MMYs from vehicle listings...")
        all_mmys = self._get_all_vehicle_mmys()

        logger.info(f"  Found {len(all_mmys)} unique MMYs in vehicle listings")

        # Step 3: Build full coverage with imputation
        logger.info("Step 3/3: Building full coverage with imputation...")
        imputed_count = 0

        for make, model, year in all_mmys:
            key_mmy = (make.upper(), model.upper(), year)

            # If we have exact MMY, use it
            if key_mmy in reviews_by_mmy:
                self._phrases_by_mmy[key_mmy] = reviews_by_mmy[key_mmy]
                continue

            # Otherwise, impute from most recent same make+model
            key_mm = (make.upper(), model.upper())
            if key_mm in reviews_by_mm:
                # Find most recent review for this make+model
                source_phrases = reviews_by_mm[key_mm][0]  # Already sorted by year desc

                # Create imputed copy
                imputed_phrases = VehiclePhrases(
                    make=make,
                    model=model,
                    year=year,  # Use requested year, not source year
                    pros_phrases=source_phrases.pros_phrases.copy(),
                    cons_phrases=source_phrases.cons_phrases.copy(),
                    pros_embeddings=source_phrases.pros_embeddings.copy(),
                    cons_embeddings=source_phrases.cons_embeddings.copy(),
                    imputed=True
                )
                self._phrases_by_mmy[key_mmy] = imputed_phrases
                imputed_count += 1
            else:
                # No reviews for this make+model at all - skip
                pass

        logger.info(f"  Imputed {imputed_count} MMYs from recent same make+model")
        logger.info(f"  Final coverage: {len(self._phrases_by_mmy)}/{len(all_mmys)} MMYs " +
                   f"({100*len(self._phrases_by_mmy)/len(all_mmys) if all_mmys else 0:.1f}%)")

    def _load_and_embed_phrases(self) -> Tuple[
        Dict[Tuple[str, str, int], VehiclePhrases],
        Dict[Tuple[str, str], List[VehiclePhrases]]
    ]:
        """
        Load reviews from Tavily DB and batch-embed INDIVIDUAL phrases.

        Returns:
            Tuple of (reviews_by_mmy, reviews_by_mm)
            - reviews_by_mmy: (make, model, year) -> VehiclePhrases
            - reviews_by_mm: (make, model) -> List[VehiclePhrases] sorted by year desc
        """
        encoder = self._get_encoder()

        # Fetch all reviews
        conn = sqlite3.connect(self.reviews_db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT make, model, year, pros, cons
            FROM vehicle_reviews
            WHERE pros IS NOT NULL AND cons IS NOT NULL
            ORDER BY make, model, year DESC
        """)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            logger.warning("No reviews found in database!")
            return {}, {}

        # Collect ALL individual phrases for batch embedding
        all_phrases = []
        phrase_info = []  # (make, model, year, pros_list, cons_list, pros_start_idx, n_pros, cons_start_idx, n_cons)

        for make, model, year, pros_json, cons_json in rows:
            try:
                pros_list = json.loads(pros_json) if pros_json else []
                cons_list = json.loads(cons_json) if cons_json else []
            except json.JSONDecodeError:
                continue

            if not pros_list or not cons_list:
                continue

            # Track indices for this MMY's phrases
            pros_start_idx = len(all_phrases)
            all_phrases.extend(pros_list)
            n_pros = len(pros_list)

            cons_start_idx = len(all_phrases)
            all_phrases.extend(cons_list)
            n_cons = len(cons_list)

            phrase_info.append((make, model, year, pros_list, cons_list, pros_start_idx, n_pros, cons_start_idx, n_cons))

        # Batch encode ALL individual phrases at once
        logger.info(f"  Encoding {len(all_phrases)} individual phrases in batch...")
        all_embeddings = encoder.encode(
            all_phrases,
            batch_size=128,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        # Build dictionaries with individual phrase embeddings
        reviews_by_mmy = {}
        reviews_by_mm = {}

        for make, model, year, pros_list, cons_list, pros_start, n_pros, cons_start, n_cons in phrase_info:
            # Extract embeddings for this MMY's phrases
            pros_embeddings = all_embeddings[pros_start:pros_start + n_pros]
            cons_embeddings = all_embeddings[cons_start:cons_start + n_cons]

            vehicle_phrases = VehiclePhrases(
                make=make,
                model=model,
                year=year,
                pros_phrases=pros_list,
                cons_phrases=cons_list,
                pros_embeddings=pros_embeddings,
                cons_embeddings=cons_embeddings,
                imputed=False
            )

            # Store by exact MMY
            key_mmy = (make.upper(), model.upper(), year)
            reviews_by_mmy[key_mmy] = vehicle_phrases

            # Store by MM for imputation
            key_mm = (make.upper(), model.upper())
            if key_mm not in reviews_by_mm:
                reviews_by_mm[key_mm] = []
            reviews_by_mm[key_mm].append(vehicle_phrases)

        # Sort each MM list by year descending (prefer most recent)
        for key in reviews_by_mm:
            reviews_by_mm[key].sort(key=lambda x: x.year, reverse=True)

        return reviews_by_mmy, reviews_by_mm

    def _get_all_vehicle_mmys(self) -> List[Tuple[str, str, int]]:
        """Get all unique (make, model, year) from vehicle listings."""
        conn = sqlite3.connect(self.vehicles_db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT make, model, year
            FROM unified_vehicle_listings
            WHERE make IS NOT NULL AND model IS NOT NULL AND year IS NOT NULL
            ORDER BY make, model, year
        """)

        mmys = cursor.fetchall()
        conn.close()

        return mmys

    def has_phrases(self, make: str, model: str, year: int) -> bool:
        """
        Check if we have phrases for this MMY.

        Args:
            make: Vehicle make
            model: Vehicle model
            year: Vehicle year

        Returns:
            True if phrases exist
        """
        key_mmy = (make.upper(), model.upper(), year)
        return key_mmy in self._phrases_by_mmy

    def get_phrases(
        self,
        make: str,
        model: str,
        year: int
    ) -> Optional[VehiclePhrases]:
        """
        Get pre-computed individual phrase embeddings for a vehicle.

        Args:
            make: Vehicle make
            model: Vehicle model
            year: Vehicle year

        Returns:
            VehiclePhrases with individual phrase embeddings (None if not found)
        """
        key_mmy = (make.upper(), model.upper(), year)
        return self._phrases_by_mmy.get(key_mmy)

    def get_phrases_batch(
        self,
        vehicles: List[Tuple[str, str, int]]
    ) -> List[Optional[VehiclePhrases]]:
        """
        Get phrases for multiple vehicles (batch lookup).

        Args:
            vehicles: List of (make, model, year) tuples

        Returns:
            List of VehiclePhrases (same order as input)
        """
        return [self.get_phrases(make, model, year) for make, model, year in vehicles]

    def encode(self, text: str) -> np.ndarray:
        """
        Encode text into embedding vector.

        Args:
            text: Text to encode

        Returns:
            Embedding vector (D,) normalized to unit length
        """
        encoder = self._get_encoder()
        embedding = encoder.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        return embedding[0]

    def encode_batch(self, texts: List[str]) -> np.ndarray:
        """
        Encode multiple texts in batch (more efficient).

        Args:
            texts: List of texts to encode

        Returns:
            Embeddings matrix (N, D) normalized to unit length
        """
        if not texts:
            return np.array([])

        encoder = self._get_encoder()
        embeddings = encoder.encode(
            texts,
            batch_size=128,
            convert_to_numpy=True,
            normalize_embeddings=True
        )
        return embeddings

    def get_coverage_stats(self) -> Dict[str, any]:
        """
        Get statistics about phrase coverage.

        Returns:
            Dict with coverage statistics
        """
        imputed_count = sum(1 for vp in self._phrases_by_mmy.values() if vp.imputed)
        native_count = len(self._phrases_by_mmy) - imputed_count

        # Calculate total phrases and memory
        total_pros_phrases = sum(len(vp.pros_phrases) for vp in self._phrases_by_mmy.values())
        total_cons_phrases = sum(len(vp.cons_phrases) for vp in self._phrases_by_mmy.values())
        avg_pros = total_pros_phrases / len(self._phrases_by_mmy) if self._phrases_by_mmy else 0
        avg_cons = total_cons_phrases / len(self._phrases_by_mmy) if self._phrases_by_mmy else 0

        return {
            "total_mmys": len(self._phrases_by_mmy),
            "native_reviews": native_count,
            "imputed": imputed_count,
            "imputation_rate": imputed_count / len(self._phrases_by_mmy) if self._phrases_by_mmy else 0.0,
            "total_pros_phrases": total_pros_phrases,
            "total_cons_phrases": total_cons_phrases,
            "avg_pros_per_vehicle": avg_pros,
            "avg_cons_per_vehicle": avg_cons,
            "memory_mb": self._estimate_memory_usage()
        }

    def _estimate_memory_usage(self) -> float:
        """Estimate memory usage in MB."""
        total_bytes = 0

        for vp in self._phrases_by_mmy.values():
            # Pros embeddings: (N_pros, 768) * 4 bytes
            total_bytes += vp.pros_embeddings.nbytes
            # Cons embeddings: (N_cons, 768) * 4 bytes
            total_bytes += vp.cons_embeddings.nbytes
            # String overhead (rough estimate)
            total_bytes += sum(len(s) for s in vp.pros_phrases) * 2
            total_bytes += sum(len(s) for s in vp.cons_phrases) * 2

        return total_bytes / (1024 * 1024)

    def __repr__(self) -> str:
        stats = self.get_coverage_stats()
        return (f"PhraseStore(mmys={stats['total_mmys']}, "
                f"native={stats['native_reviews']}, imputed={stats['imputed']}, "
                f"phrases={stats['total_pros_phrases']+stats['total_cons_phrases']}, "
                f"mem={stats['memory_mb']:.1f}MB)")
