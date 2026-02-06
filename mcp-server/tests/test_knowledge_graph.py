"""Unit tests for KnowledgeGraphBuilder (knowledge graph node creation)."""

import pytest
from unittest.mock import Mock, MagicMock, patch

from app.knowledge_graph import KnowledgeGraphBuilder


def _make_mock_connection():
    """Create a mock Neo4j connection for unit tests."""
    mock_conn = Mock()
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.single.return_value = {"product_id": "test-product-123"}
    mock_session.run.return_value = mock_result
    mock_conn.driver.session.return_value.__enter__ = Mock(return_value=mock_session)
    mock_conn.driver.session.return_value.__exit__ = Mock(return_value=False)
    mock_conn.database = "neo4j"
    return mock_conn, mock_session


class TestKnowledgeGraphBuilder:
    """Tests for KnowledgeGraphBuilder node creation methods."""

    def test_create_laptop_node_returns_product_id(self):
        """create_laptop_node should return the product_id from the result."""
        mock_conn, mock_session = _make_mock_connection()
        builder = KnowledgeGraphBuilder(mock_conn)

        laptop_data = {
            "product_id": "laptop-001",
            "name": "Dell XPS 15",
            "brand": "Dell",
            "model": "XPS 15",
            "price": 1299.99,
            "description": "High-performance laptop",
            "image_url": "https://example.com/img.jpg",
            "subcategory": "Work",
            "available": True,
            "weight_kg": 2.0,
            "portability_score": 85,
            "battery_life_hours": 10,
            "screen_size_inches": 15.6,
            "refresh_rate_hz": 60,
            "manufacturer_country": "USA",
            "manufacturer_founded": 1984,
            "manufacturer_website": "dell.com",
            "cpu_model": "Intel i7",
            "cpu_manufacturer": "Intel",
            "cpu_cores": 8,
            "cpu_threads": 16,
            "cpu_base_clock": 2.4,
            "cpu_boost_clock": 4.5,
            "cpu_tdp": 45,
            "cpu_generation": "12th Gen",
            "cpu_tier": "Mid-range",
            "gpu_model": None,
            "gpu_manufacturer": None,
            "gpu_vram": None,
            "gpu_memory_type": None,
            "gpu_tdp": None,
            "gpu_tier": None,
            "gpu_ray_tracing": False,
            "ram_capacity": 16,
            "ram_type": "DDR4",
            "ram_speed": 3200,
            "ram_channels": 2,
            "ram_expandable": True,
            "storage_capacity": 512,
            "storage_type": "NVMe SSD",
            "storage_interface": "PCIe 4.0",
            "storage_read_speed": 7000,
            "storage_write_speed": 5000,
            "storage_expandable": True,
            "display_resolution": "1920x1080",
            "display_panel_type": "IPS",
            "display_brightness": 300,
            "display_color_gamut": "sRGB 100%",
            "display_touch": False,
        }

        result = builder.create_laptop_node(laptop_data)

        assert result == "test-product-123"
        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["product_id"] == "laptop-001"
        assert params["name"] == "Dell XPS 15"
        assert params["brand"] == "Dell"

    def test_create_book_node_returns_product_id(self):
        """create_book_node should return the product_id from the result."""
        mock_conn, mock_session = _make_mock_connection()
        builder = KnowledgeGraphBuilder(mock_conn)

        book_data = {
            "product_id": "book-001",
            "title": "The Great Novel",
            "name": "The Great Novel by Jane Doe",
            "price": 19.99,
            "description": "A great book",
            "image_url": "https://example.com/book.jpg",
            "isbn": "978-1234567890",
            "pages": 400,
            "language": "English",
            "publication_year": 2023,
            "edition": "1st",
            "format": "Paperback",
            "available": True,
            "author": "Jane Doe",
            "author_nationality": "American",
            "author_birth_year": 1980,
            "author_biography": "Author bio",
            "author_awards": [],
            "publisher": "Penguin",
            "publisher_country": "USA",
            "publisher_founded": 1935,
            "publisher_website": "penguin.com",
            "genre": "Fiction",
            "genre_description": "Fiction books",
            "themes": ["Love", "Adventure"],
            "series_name": None,
            "series_position": None,
            "series_total_books": None,
        }

        result = builder.create_book_node(book_data)

        assert result == "test-product-123"
        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["product_id"] == "book-001"
        assert params["title"] == "The Great Novel"
        assert params["author"] == "Jane Doe"

    def test_create_jewelry_node_returns_product_id(self):
        """create_jewelry_node should return the product_id from the result."""
        mock_conn, mock_session = _make_mock_connection()
        builder = KnowledgeGraphBuilder(mock_conn)

        jewelry_data = {
            "product_id": "jewelry-001",
            "name": "Gold Necklace",
            "brand": "Tiffany",
            "price": 299.99,
            "description": "Elegant gold necklace",
            "image_url": "https://example.com/necklace.jpg",
            "subcategory": "Necklaces",
            "color": "Gold",
            "available": True,
            "material": "Gold",
            "item_type": "Necklaces",
        }

        result = builder.create_jewelry_node(jewelry_data)

        assert result == "test-product-123"
        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["product_id"] == "jewelry-001"
        assert params["brand"] == "Tiffany"
        assert params["material"] == "Gold"

    def test_create_accessory_node_returns_product_id(self):
        """create_accessory_node should return the product_id from the result."""
        mock_conn, mock_session = _make_mock_connection()
        builder = KnowledgeGraphBuilder(mock_conn)

        accessory_data = {
            "product_id": "acc-001",
            "name": "Leather Belt",
            "brand": "Gucci",
            "price": 89.99,
            "description": "Classic leather belt",
            "image_url": "https://example.com/belt.jpg",
            "subcategory": "Belts",
            "color": "Brown",
            "available": True,
            "item_type": "Belts",
        }

        result = builder.create_accessory_node(accessory_data)

        assert result == "test-product-123"
        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["product_id"] == "acc-001"
        assert params["item_type"] == "Belts"

    def test_create_generic_product_node_returns_product_id(self):
        """create_generic_product_node should return the product_id from the result."""
        mock_conn, mock_session = _make_mock_connection()
        builder = KnowledgeGraphBuilder(mock_conn)

        product_data = {
            "product_id": "beauty-001",
            "name": "Lipstick Set",
            "brand": "MAC",
            "price": 24.99,
            "description": "Matte lipstick set",
            "image_url": "https://example.com/lipstick.jpg",
            "category": "Beauty",
            "subcategory": "Makeup",
            "product_type": "lipstick",
            "color": "Red",
            "available": True,
        }

        result = builder.create_generic_product_node(product_data)

        assert result == "test-product-123"
        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["product_id"] == "beauty-001"
        assert params["category"] == "Beauty"

    def test_create_comparison_relationships_calls_session_run(self):
        """create_comparison_relationships should run a MERGE query."""
        mock_conn, mock_session = _make_mock_connection()
        mock_session.run.return_value = None  # No result for this call
        builder = KnowledgeGraphBuilder(mock_conn)

        builder.create_comparison_relationships("prod-1", "prod-2", "SIMILAR_TO", 0.85)

        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["product_id1"] == "prod-1"
        assert params["product_id2"] == "prod-2"
        assert params["score"] == 0.85

    def test_create_genre_hierarchy_calls_session_run(self):
        """create_genre_hierarchy should run UNWIND query with genre_data."""
        mock_conn, mock_session = _make_mock_connection()
        mock_session.run.return_value = None
        builder = KnowledgeGraphBuilder(mock_conn)

        genre_data = [
            {"name": "Fiction", "description": "Fiction books", "level": 1, "parent_genres": []},
            {"name": "Sci-Fi", "description": "Science fiction", "level": 2, "parent_genres": ["Fiction"]},
        ]

        builder.create_genre_hierarchy(genre_data)

        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["genre_data"] == genre_data

    def test_create_session_memory_calls_session_run(self):
        """create_session_memory should persist session intent, step intent, important_info (kg.txt)."""
        mock_conn, mock_session = _make_mock_connection()
        mock_session.run.return_value = None
        builder = KnowledgeGraphBuilder(mock_conn)

        builder.create_session_memory(
            session_id="sess-123",
            user_id="user-1",
            session_intent="Decide today",
            step_intent="Compare",
            important_info={"active_domain": "laptops", "filters": {"brand": "Dell"}},
        )

        mock_session.run.assert_called_once()
        args, kwargs = mock_session.run.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["session_id"] == "sess-123"
        assert params["session_intent"] == "Decide today"
        assert params["step_intent"] == "Compare"
        assert "active_domain" in params["important_info_json"] or "laptops" in params["important_info_json"]

    def test_get_session_memory_returns_dict_or_none(self):
        """get_session_memory should return dict with session_intent, step_intent, important_info or None."""
        mock_conn, mock_session = _make_mock_connection()
        mock_record = {"session_intent": "Explore", "step_intent": "Research", "important_info": "{}"}
        mock_result = MagicMock()
        mock_result.single.return_value = mock_record
        mock_session.run.return_value = mock_result
        builder = KnowledgeGraphBuilder(mock_conn)

        result = builder.get_session_memory("sess-123")

        assert result is not None
        assert result["session_intent"] == "Explore"
        assert result["step_intent"] == "Research"
        assert "important_info" in result

    def test_get_graph_statistics_returns_dict(self):
        """get_graph_statistics should return a dict with node/relationship counts."""
        mock_conn = Mock()
        mock_session = MagicMock()

        def run_side_effect(query, *args, **kwargs):
            r = MagicMock()
            if "node_types" in query or "relationship_types" in query:
                r.__iter__ = lambda: iter([{"label": "Product", "count": 100}])
                return r
            r.single.return_value = {"count": 50}
            return r

        mock_session.run.side_effect = run_side_effect
        mock_conn.driver.session.return_value.__enter__ = Mock(return_value=mock_session)
        mock_conn.driver.session.return_value.__exit__ = Mock(return_value=False)
        mock_conn.database = "neo4j"

        builder = KnowledgeGraphBuilder(mock_conn)
        stats = builder.get_graph_statistics()

        assert isinstance(stats, dict)
        assert "total_nodes" in stats
        assert "node_types" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
