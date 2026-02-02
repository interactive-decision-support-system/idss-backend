"""
Unit Tests for Merchant Feed Exporter.

Tests export formats (JSON, XML, CSV) and validation.
"""

import pytest
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.merchant_feed import MerchantFeedExporter


class TestMerchantFeedExporter:
    """Test suite for Merchant Feed Exporter."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.exporter = MerchantFeedExporter(base_url="https://test.com")
        self.mock_products = self._create_mock_products()
    
    def _create_mock_products(self):
        """Create mock product objects for testing."""
        products = []
        
        for i in range(3):
            product = Mock()
            product.product_id = f"PROD-{i:03d}"
            product.name = f"Test Product {i}"
            product.description = f"Description for product {i}"
            product.category = "electronics"
            product.brand = "TestBrand"
            product.created_at = datetime(2026, 1, 22, 10, 0, 0)
            product.updated_at = datetime(2026, 1, 22, 10, 0, 0)
            product.product_type = "ecommerce"
            product.metadata = {
                "sku": f"SKU-{i:03d}",
                "primary_image": f"https://test.com/images/prod-{i}.jpg"
            }
            
            # Mock relationships
            product.price_info = Mock()
            product.price_info.price_cents = 9999
            product.price_info.currency = "USD"
            
            product.inventory_info = Mock()
            product.inventory_info.available_qty = 10 if i < 2 else 0  # Last one out of stock
            
            products.append(product)
        
        return products
    
    # ========================================================================
    # Test JSON Export
    # ========================================================================
    
    def test_export_json_basic(self):
        """Test basic JSON feed export."""
        feed = self.exporter.export_json(self.mock_products, include_metadata=False)
        
        assert feed["version"] == "1.0"
        assert feed["total_count"] == 3
        assert len(feed["products"]) == 3
        
        # Check first product
        product = feed["products"][0]
        assert product["id"] == "PROD-000"
        assert product["title"] == "Test Product 0"
        assert product["price"]["value"] == 99.99
        assert product["price"]["currency"] == "USD"
        assert product["availability"] == "in stock"
    
    def test_export_json_with_metadata(self):
        """Test JSON export with MCP metadata."""
        feed = self.exporter.export_json(self.mock_products, include_metadata=True)
        
        product = feed["products"][0]
        assert "mcp_metadata" in product
        assert product["mcp_metadata"]["product_type"] == "ecommerce"
    
    def test_export_json_out_of_stock(self):
        """Test JSON export correctly maps out of stock items."""
        feed = self.exporter.export_json(self.mock_products)
        
        # Last product should be out of stock
        assert feed["products"][2]["availability"] == "out of stock"
    
    def test_export_json_with_images(self):
        """Test JSON export includes image links."""
        feed = self.exporter.export_json(self.mock_products)
        
        product = feed["products"][0]
        assert "image_link" in product
        assert "prod-0.jpg" in product["image_link"]
    
    # ========================================================================
    # Test XML Export
    # ========================================================================
    
    def test_export_xml_structure(self):
        """Test XML feed export has correct structure."""
        xml_string = self.exporter.export_xml(self.mock_products)
        
        # Parse XML
        root = ET.fromstring(xml_string)
        
        # Handle namespace in tag
        assert "feed" in root.tag
        
        # Define namespaces
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        
        title = root.find("atom:title", ns)
        assert title is not None and title.text == "MCP Product Feed"
        
        # Count entries
        entries = root.findall("atom:entry", ns)
        assert len(entries) == 3
    
    def test_export_xml_product_fields(self):
        """Test XML export includes all required fields."""
        xml_string = self.exporter.export_xml(self.mock_products)
        root = ET.fromstring(xml_string)
        
        # Define namespaces
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "g": "http://base.google.com/ns/1.0"
        }
        
        # Check first entry
        entry = root.find("atom:entry", ns)
        assert entry is not None
        
        # Required fields (with g: namespace)
        assert entry.find("g:id", ns).text == "PROD-000"
        assert entry.find("g:title", ns).text == "Test Product 0"
        assert entry.find("g:description", ns) is not None
        assert entry.find("g:link", ns).text == "https://test.com/products/PROD-000"
        assert entry.find("g:price", ns).text == "99.99 USD"
        assert entry.find("g:availability", ns).text == "in stock"
    
    def test_export_xml_optional_fields(self):
        """Test XML export includes optional fields when available."""
        xml_string = self.exporter.export_xml(self.mock_products)
        root = ET.fromstring(xml_string)
        
        # Define namespaces
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "g": "http://base.google.com/ns/1.0"
        }
        
        entry = root.find("atom:entry", ns)
        assert entry is not None
        
        assert entry.find("g:product_type", ns).text == "electronics"
        assert entry.find("g:brand", ns).text == "TestBrand"
        assert entry.find("g:image_link", ns) is not None
    
    # ========================================================================
    # Test CSV Export
    # ========================================================================
    
    def test_export_csv_structure(self):
        """Test CSV export has correct structure."""
        csv_string = self.exporter.export_csv(self.mock_products)
        
        lines = csv_string.split("\n")
        assert len(lines) == 4  # Header + 3 products
        
        # Check header
        header = lines[0]
        assert "id" in header
        assert "title" in header
        assert "price" in header
    
    def test_export_csv_escaping(self):
        """Test CSV export properly escapes special characters."""
        # Create product with comma in description
        product = self.mock_products[0]
        product.description = "Test, with comma"
        
        csv_string = self.exporter.export_csv([product])
        lines = csv_string.split("\n")
        
        # Should have quotes around the description
        assert '"Test, with comma"' in lines[1]
    
    # ========================================================================
    # Test Feed Validation
    # ========================================================================
    
    def test_validate_feed_valid(self):
        """Test feed validation with valid feed."""
        feed = self.exporter.export_json(self.mock_products)
        
        validation = self.exporter.validate_feed(feed)
        
        assert validation["valid"] is True
        assert validation["error_count"] == 0
    
    def test_validate_feed_missing_required_field(self):
        """Test feed validation catches missing required fields."""
        feed = {
            "products": [{
                "id": "PROD-001",
                # Missing title (required)
                "price": {"value": 99.99, "currency": "USD"},
                "availability": "in stock"
            }]
        }
        
        validation = self.exporter.validate_feed(feed)
        
        assert validation["valid"] is False
        assert validation["error_count"] > 0
        assert any("title" in error.lower() for error in validation["errors"])
    
    def test_validate_feed_warnings(self):
        """Test feed validation generates warnings for optional fields."""
        feed = {
            "products": [{
                "id": "PROD-001",
                "title": "Test",
                "description": "Test",
                "link": "https://test.com/1",
                "price": {"value": 99.99, "currency": "USD"},
                "availability": "in stock"
                # Missing image_link (optional but recommended)
            }]
        }
        
        validation = self.exporter.validate_feed(feed)
        
        assert validation["valid"] is True
        assert validation["warning_count"] > 0
        assert any("image_link" in warning.lower() for warning in validation["warnings"])
    
    def test_validate_feed_empty(self):
        """Test feed validation catches empty feeds."""
        feed = {"products": []}
        
        validation = self.exporter.validate_feed(feed)
        
        assert validation["valid"] is False
        assert any("no products" in error.lower() for error in validation["errors"])
    
    # ========================================================================
    # Test Availability Mapping
    # ========================================================================
    
    def test_availability_mapping_in_stock(self):
        """Test availability mapping for in stock items."""
        assert self.exporter._map_availability(1) == "in stock"
        assert self.exporter._map_availability(100) == "in stock"
    
    def test_availability_mapping_out_of_stock(self):
        """Test availability mapping for out of stock items."""
        assert self.exporter._map_availability(0) == "out of stock"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
