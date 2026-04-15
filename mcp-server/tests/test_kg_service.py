"""Tests for KnowledgeGraphService (Neo4j KG integration)."""

import os
import pytest

from app.kg_service import KnowledgeGraphService, NEO4J_AVAILABLE


NEO4J_ENV_READY = all(
    os.getenv(key) for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> KnowledgeGraphService:
    return KnowledgeGraphService(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )


# ---------------------------------------------------------------------------
# Offline unit tests (no Neo4j required)
# ---------------------------------------------------------------------------

def test_kg_unavailable_without_password():
    """If Neo4j is not configured, service should report unavailable."""
    service = KnowledgeGraphService(password=None)
    try:
        assert service.is_available() is False
    finally:
        service.close()


def test_kg_unavailable_returns_empty_lists():
    """All traversal methods return [] when KG is unavailable (no password)."""
    svc = KnowledgeGraphService(password=None)
    assert svc.get_similar_products("any-id") == []
    assert svc.get_better_than("any-id") == []
    assert svc.get_diverse_alternatives(["any-id"]) == []
    assert svc.get_compatible_components("any-id") == []
    ids, explanation = svc.search_candidates("gaming laptop", {})
    assert ids == []
    assert isinstance(explanation, dict)
    svc.close()


def test_kg_diverse_alternatives_empty_input():
    """get_diverse_alternatives([]) should return [] without calling Neo4j."""
    svc = KnowledgeGraphService(password=None)
    assert svc.get_diverse_alternatives([]) == []
    svc.close()


def test_build_cypher_query_hard_constraints():
    """Verify price and brand become hard WHERE conditions, not soft scoring."""
    svc = KnowledgeGraphService(password=None)
    cypher = svc._build_cypher_query(
        query="",
        filters={"category": "Electronics", "price_max_cents": 80000, "brand": "Dell"},
        limit=10,
    )
    # Hard constraints must appear in WHERE clause
    assert "p.price <= $price_max" in cypher
    assert "p.brand = $brand" in cypher
    # Use-case flags absent from filters → should NOT appear as hard WHERE conditions
    assert "good_for_" not in cypher.split("WHERE")[1].split("WITH")[0]
    svc.close()


def test_build_cypher_query_soft_constraints_become_case_when():
    """Use-case flags (good_for_gaming etc.) should be soft CASE WHEN scores."""
    svc = KnowledgeGraphService(password=None)
    cypher = svc._build_cypher_query(
        query="gaming laptop",
        filters={"category": "Electronics", "good_for_gaming": True},
        limit=10,
    )
    # Soft: use-case flag is a CASE WHEN, not a hard WHERE condition
    assert "CASE WHEN p.good_for_gaming" in cypher
    # Hard WHERE should NOT contain the use-case flag as a bare condition
    where_part = cypher.split("WHERE")[1].split("WITH")[0] if "WITH" in cypher else cypher
    assert "p.good_for_gaming = true" not in where_part
    # Connectivity bonus via OPTIONAL MATCH
    assert "OPTIONAL MATCH" in cypher
    assert "SIMILAR_TO" in cypher
    svc.close()


def test_build_cypher_query_no_soft_constraints_uses_connectivity():
    """When no use-case flags or text query, Cypher should still use connectivity ordering."""
    svc = KnowledgeGraphService(password=None)
    cypher = svc._build_cypher_query(
        query="",
        filters={"category": "Electronics"},
        limit=5,
    )
    assert "OPTIONAL MATCH" in cypher
    assert "connectivity" in cypher
    svc.close()


def test_tokenize_query_basic():
    """Whitespace-split, lowercased, deduped, ≥2 chars, trailing punctuation stripped."""
    assert KnowledgeGraphService._tokenize_query("Fantasy Novel") == ["fantasy", "novel"]
    assert KnowledgeGraphService._tokenize_query("") == []
    assert KnowledgeGraphService._tokenize_query("a bb a cc") == ["bb", "cc"]
    assert KnowledgeGraphService._tokenize_query("book, hard-cover!") == ["book", "hard-cover"]


def test_tokenize_query_caps_and_dedupes():
    """Dedup preserves first occurrence; cap prevents pathological expansion."""
    assert KnowledgeGraphService._tokenize_query("ml ML Ml") == ["ml"]
    many = " ".join(f"tok{i}" for i in range(30))
    assert len(KnowledgeGraphService._tokenize_query(many, max_tokens=5)) == 5


def test_build_cypher_query_multiword_adds_per_token_cases():
    """Multi-word query emits one +1 CASE WHEN per token plus the phrase +3."""
    svc = KnowledgeGraphService(password=None)
    cypher = svc._build_cypher_query(
        query="fantasy novel",
        filters={"category": "Books"},
        limit=10,
    )
    # Phrase-level match still present
    assert "CONTAINS $q" in cypher
    # Per-token matches present with deterministic naming
    assert "CONTAINS $q_tok_0" in cypher
    assert "CONTAINS $q_tok_1" in cypher
    svc.close()


def test_build_cypher_query_single_token_skips_per_token_layer():
    """Single-token query → phrase match only; no redundant $q_tok_0 clauses."""
    svc = KnowledgeGraphService(password=None)
    cypher = svc._build_cypher_query(
        query="laptop",
        filters={"category": "Electronics"},
        limit=10,
    )
    assert "CONTAINS $q" in cypher
    assert "$q_tok_" not in cypher
    svc.close()


# ---------------------------------------------------------------------------
# Diversity score helper (no Neo4j required)
# ---------------------------------------------------------------------------

def test_compute_diversity_score_all_same_brand():
    """When every product is the same brand, brand_diversity should be 1/n."""
    # Import from chat_endpoint to test the utility directly
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../agent"))
    try:
        from chat_endpoint import _compute_diversity_score
        products = [
            {"brand": "Dell", "price": 500},
            {"brand": "Dell", "price": 700},
            {"brand": "Dell", "price": 900},
        ]
        result = _compute_diversity_score(products)
        assert result["brand_diversity"] == pytest.approx(1 / 3, abs=0.01)
        assert result["price_spread"] > 0
        assert 0 <= result["overall"] <= 1
    except ImportError:
        pytest.skip("chat_endpoint not importable from this test path")


def test_compute_diversity_score_all_different_brands():
    """Unique brands → brand_diversity == 1.0."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../agent"))
    try:
        from chat_endpoint import _compute_diversity_score
        products = [
            {"brand": "Dell", "price": 500},
            {"brand": "Apple", "price": 1500},
            {"brand": "Lenovo", "price": 800},
        ]
        result = _compute_diversity_score(products)
        assert result["brand_diversity"] == 1.0
        assert result["price_spread"] == pytest.approx((1500 - 500) / 1500, abs=0.01)
    except ImportError:
        pytest.skip("chat_endpoint not importable from this test path")


def test_compute_diversity_score_empty():
    """Empty list should return all zeros without error."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../agent"))
    try:
        from chat_endpoint import _compute_diversity_score
        result = _compute_diversity_score([])
        assert result == {"brand_diversity": 0.0, "price_spread": 0.0, "overall": 0.0}
    except ImportError:
        pytest.skip("chat_endpoint not importable from this test path")


# ---------------------------------------------------------------------------
# Live Neo4j tests (skipped when Neo4j not configured)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_connection_available():
    """Ensure KG reports availability when configured."""
    service = _make_service()
    try:
        assert service.is_available() is True
    finally:
        service.close()


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_search_candidates_returns_list():
    """Basic KG search should return list output and explanation dict."""
    service = _make_service()
    try:
        product_ids, explanation = service.search_candidates(
            "gaming laptop", {"category": "Electronics"}, limit=5
        )
        assert isinstance(product_ids, list)
        assert isinstance(explanation, dict)
    finally:
        service.close()


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_get_better_than_returns_list():
    """get_better_than should return a list (may be empty if no BETTER_THAN edges)."""
    service = _make_service()
    try:
        # Use a dummy ID — the graph may not have this product, so [] is valid
        result = service.get_better_than("nonexistent-id", limit=3)
        assert isinstance(result, list)
    finally:
        service.close()


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_get_diverse_alternatives_returns_list():
    """get_diverse_alternatives should return a list (may be empty if KG not populated)."""
    service = _make_service()
    try:
        result = service.get_diverse_alternatives(["nonexistent-id-1", "nonexistent-id-2"])
        assert isinstance(result, list)
        # IDs in result should NOT overlap with input
        assert not set(result) & {"nonexistent-id-1", "nonexistent-id-2"}
    finally:
        service.close()


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_kg_soft_constraint_search_returns_results():
    """Search with use-case flags (soft constraints) should not return empty when
    products exist in that category — flags boost but don't filter out all results."""
    service = _make_service()
    try:
        # Searching with good_for_gaming as a soft constraint.
        # Even if no product has good_for_gaming=true, we should still get results
        # because it's now a CASE WHEN score, not a hard WHERE filter.
        ids, _ = service.search_candidates(
            query="laptop",
            filters={"category": "Electronics", "good_for_gaming": True},
            limit=5,
        )
        # With soft constraints the query should not fail (empty list is OK if KG unpopulated)
        assert isinstance(ids, list)
    finally:
        service.close()
