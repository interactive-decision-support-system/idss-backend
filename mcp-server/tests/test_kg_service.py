"""Tests for KnowledgeGraphService (Neo4j KG integration)."""

import os
import pytest

from app.kg_service import KnowledgeGraphService, NEO4J_AVAILABLE


NEO4J_ENV_READY = all( 
    os.getenv(key) for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
)


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_connection_available():
    """Ensure KG reports availability when configured."""
    service = KnowledgeGraphService(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )
    try:
        assert service.is_available() is True
    finally:
        service.close()


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_search_candidates_returns_list():
    """Basic KG search should return list output and explanation dict."""
    service = KnowledgeGraphService(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )
    try:
        product_ids, explanation = service.search_candidates("gaming laptop", {"category": "Electronics"}, limit=5)
        assert isinstance(product_ids, list)
        assert isinstance(explanation, dict)
    finally:
        service.close()


def test_kg_unavailable_without_password():
    """If Neo4j is not configured, service should report unavailable."""
    service = KnowledgeGraphService(password=None)
    try:
        assert service.is_available() is False
    finally:
        service.close()
