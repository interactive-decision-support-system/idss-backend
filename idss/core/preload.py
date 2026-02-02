"""
Preload module for IDSS.

Preloads all heavy resources at server startup to avoid slow first requests:
- Vehicle database connection
- FAISS index for embedding similarity
- Sentence transformer models
- Phrase embeddings for coverage-risk

Usage:
    from idss.core.preload import preload_all
    preload_all()  # Call at server startup
"""
import time
from typing import Optional

from idss.utils.logger import get_logger
from idss.core.config import get_config

logger = get_logger("core.preload")


def preload_all(
    preload_embedding_similarity: bool = True,
    preload_coverage_risk: bool = True,
    preload_database: bool = True
) -> dict:
    """
    Preload all heavy resources at startup.

    Args:
        preload_embedding_similarity: Load FAISS index and encoder for embedding similarity
        preload_coverage_risk: Load phrase embeddings and encoder for coverage-risk
        preload_database: Initialize database connection

    Returns:
        Dict with timing info for each component
    """
    total_start = time.time()
    timings = {}

    logger.info("=" * 60)
    logger.info("PRELOADING RESOURCES...")
    logger.info("=" * 60)

    # 1. Preload database connection
    if preload_database:
        start = time.time()
        try:
            from idss.data.vehicle_store import LocalVehicleStore
            store = LocalVehicleStore(require_photos=True)
            # Do a simple query to warm up the connection
            store.search_listings({"year": "2024"}, limit=1)
            timings["database"] = time.time() - start
            logger.info(f"[OK] Database connection ({timings['database']:.2f}s)")
        except Exception as e:
            logger.error(f"[FAIL] Database preload failed: {e}")
            timings["database"] = -1

    # 2. Preload embedding similarity components (FAISS + encoder)
    if preload_embedding_similarity:
        start = time.time()
        try:
            from idss.recommendation.dense_ranker import get_dense_embedding_store

            # Get the cached store (loads FAISS index)
            dense_store = get_dense_embedding_store()

            # Force load the encoder by encoding a test query
            _ = dense_store.encode_text("test query for preloading")

            timings["embedding_similarity"] = time.time() - start
            logger.info(f"[OK] Embedding Similarity - FAISS + encoder ({timings['embedding_similarity']:.2f}s)")
        except Exception as e:
            logger.error(f"[FAIL] Embedding similarity preload failed: {e}")
            timings["embedding_similarity"] = -1

    # 3. Preload coverage-risk components (phrase embeddings + encoder)
    if preload_coverage_risk:
        start = time.time()
        try:
            from idss.recommendation.coverage_risk import get_phrase_store

            # Get the cached store (loads phrase embeddings)
            phrase_store = get_phrase_store()

            # Force load the encoder by encoding a test preference
            _ = phrase_store.encode_batch(["test preference for preloading"])

            timings["coverage_risk"] = time.time() - start
            logger.info(f"[OK] Coverage-Risk - phrases + encoder ({timings['coverage_risk']:.2f}s)")
        except Exception as e:
            logger.error(f"[FAIL] Coverage-risk preload failed: {e}")
            timings["coverage_risk"] = -1

    total_time = time.time() - total_start
    timings["total"] = total_time

    logger.info("=" * 60)
    logger.info(f"PRELOAD COMPLETE ({total_time:.2f}s total)")
    logger.info("=" * 60)

    return timings


def preload_for_method(method: Optional[str] = None) -> dict:
    """
    Preload only the resources needed for a specific method.

    Args:
        method: 'embedding_similarity' or 'coverage_risk'.
                If None, uses config setting.

    Returns:
        Dict with timing info
    """
    if method is None:
        config = get_config()
        method = config.recommendation_method

    logger.info(f"Preloading for method: {method}")

    return preload_all(
        preload_embedding_similarity=(method == "embedding_similarity"),
        preload_coverage_risk=(method == "coverage_risk"),
        preload_database=True
    )


if __name__ == "__main__":
    # Test preloading
    preload_all()
