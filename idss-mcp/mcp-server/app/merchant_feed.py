"""
Google Merchant Center Product Feed Export.

Exports MCP products in Google Shopping feed format (XML/JSON).
Based on Google's product data specification.

Required fields per Google Merchant Center:
- id: Product identifier
- title: Product name
- description: Product description
- link: Product page URL
- image_link: Primary product image
- price: Price with currency
- availability: in stock | out of stock | preorder

Reference: https://github.com/Universal-Commerce-Protocol (UCP spec)
"""

import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.models import Product
from app.schemas import ProductSummary


class MerchantFeedExporter:
    """
    Export products in Google Merchant Center compatible format.
    
    Supports:
    - XML (Google Shopping Content API format)
    - JSON (for programmatic access)
    - Validation against required fields
    - Multi-product type support (vehicles, e-commerce, real estate, travel)
    """
    
    def __init__(self, base_url: str = "https://your-domain.com"):
        """
        Initialize exporter.
        
        Args:
            base_url: Base URL for product links
        """
        self.base_url = base_url.rstrip("/")
    
    def export_json(
        self,
        products: List[Product],
        include_metadata: bool = False
    ) -> Dict[str, Any]:
        """
        Export products as JSON feed.
        
        Args:
            products: List of Product models
            include_metadata: Whether to include MCP-specific metadata
        
        Returns:
            JSON feed with products array
        """
        feed = {
            "version": "1.0",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_count": len(products),
            "products": []
        }
        
        for product in products:
            product_data = {
                "id": product.product_id,
                "title": product.name,
                "description": product.description or "",
                "link": f"{self.base_url}/products/{product.product_id}",
                "price": {
                    "value": product.price_info.price_cents / 100,
                    "currency": product.price_info.currency
                },
                "availability": self._map_availability(product.inventory_info.available_qty),
            }
            
            # Add optional fields if available
            if product.category:
                product_data["product_type"] = product.category
            
            if product.brand:
                product_data["brand"] = product.brand
            
            # Add image if available in metadata
            if hasattr(product, 'metadata') and product.metadata:
                if isinstance(product.metadata, dict):
                    if "primary_image" in product.metadata:
                        product_data["image_link"] = product.metadata["primary_image"]
                    elif "images" in product.metadata and product.metadata["images"]:
                        product_data["image_link"] = product.metadata["images"][0]
            
            # Include MCP metadata if requested
            if include_metadata:
                product_data["mcp_metadata"] = {
                    "product_type": getattr(product, 'product_type', None),
                    "created_at": product.created_at.isoformat(),
                    "updated_at": product.updated_at.isoformat()
                }
            
            feed["products"].append(product_data)
        
        return feed
    
    def export_xml(
        self,
        products: List[Product]
    ) -> str:
        """
        Export products as XML feed (Google Shopping Content API format).
        
        Args:
            products: List of Product models
        
        Returns:
            XML string
        """
        # Create root element
        root = ET.Element("feed", {
            "xmlns": "http://www.w3.org/2005/Atom",
            "xmlns:g": "http://base.google.com/ns/1.0"
        })
        
        # Add feed metadata
        ET.SubElement(root, "title").text = "MCP Product Feed"
        ET.SubElement(root, "link", {
            "rel": "self",
            "href": f"{self.base_url}/export/merchant-feed.xml"
        })
        ET.SubElement(root, "updated").text = datetime.utcnow().isoformat() + "Z"
        
        # Add each product as entry
        for product in products:
            entry = ET.SubElement(root, "entry")
            
            # Required fields
            ET.SubElement(entry, "g:id").text = product.product_id
            ET.SubElement(entry, "g:title").text = product.name
            ET.SubElement(entry, "g:description").text = product.description or ""
            ET.SubElement(entry, "g:link").text = f"{self.base_url}/products/{product.product_id}"
            
            # Price
            price_value = product.price_info.price_cents / 100
            ET.SubElement(entry, "g:price").text = f"{price_value} {product.price_info.currency}"
            
            # Availability
            availability = self._map_availability(product.inventory_info.available_qty)
            ET.SubElement(entry, "g:availability").text = availability
            
            # Optional fields
            if product.category:
                ET.SubElement(entry, "g:product_type").text = product.category
            
            if product.brand:
                ET.SubElement(entry, "g:brand").text = product.brand
            
            # Image
            if hasattr(product, 'metadata') and product.metadata:
                if isinstance(product.metadata, dict):
                    image_url = None
                    if "primary_image" in product.metadata:
                        image_url = product.metadata["primary_image"]
                    elif "images" in product.metadata and product.metadata["images"]:
                        image_url = product.metadata["images"][0]
                    
                    if image_url:
                        ET.SubElement(entry, "g:image_link").text = image_url
        
        # Convert to string with pretty printing
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", method="xml")
    
    def export_csv(
        self,
        products: List[Product]
    ) -> str:
        """
        Export products as CSV (simple format).
        
        Args:
            products: List of Product models
        
        Returns:
            CSV string
        """
        lines = [
            "id,title,description,link,price,currency,availability,category,brand"
        ]
        
        for product in products:
            price_value = product.price_info.price_cents / 100
            availability = self._map_availability(product.inventory_info.available_qty)
            
            # Escape commas and quotes in text fields
            def escape(text):
                if text is None:
                    return ""
                text = str(text).replace('"', '""')
                if "," in text or '"' in text:
                    return f'"{text}"'
                return text
            
            line = ",".join([
                escape(product.product_id),
                escape(product.name),
                escape(product.description),
                escape(f"{self.base_url}/products/{product.product_id}"),
                str(price_value),
                escape(product.price_info.currency),
                escape(availability),
                escape(product.category),
                escape(product.brand)
            ])
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def _map_availability(self, available_qty: int) -> str:
        """
        Map inventory quantity to Google Merchant Center availability values.
        
        Args:
            available_qty: Number of items available
        
        Returns:
            "in stock" | "out of stock" | "preorder"
        """
        if available_qty > 0:
            return "in stock"
        else:
            return "out of stock"
    
    def validate_feed(
        self,
        feed: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate feed against Google Merchant Center requirements.
        
        Args:
            feed: JSON feed to validate
        
        Returns:
            Validation result with errors/warnings
        """
        errors = []
        warnings = []
        
        products = feed.get("products", [])
        if not products:
            errors.append("Feed contains no products")
        
        required_fields = ["id", "title", "description", "link", "price", "availability"]
        
        for i, product in enumerate(products):
            # Check required fields
            for field in required_fields:
                if field not in product or not product[field]:
                    errors.append(f"Product {i}: Missing required field '{field}'")
            
            # Check price format
            if "price" in product:
                if isinstance(product["price"], dict):
                    if "value" not in product["price"]:
                        errors.append(f"Product {i}: Price missing 'value'")
                    if "currency" not in product["price"]:
                        errors.append(f"Product {i}: Price missing 'currency'")
            
            # Warnings for optional but recommended fields
            if "image_link" not in product:
                warnings.append(f"Product {i} ({product.get('id')}): No image_link (recommended)")
            
            if "brand" not in product:
                warnings.append(f"Product {i} ({product.get('id')}): No brand (recommended)")
        
        return {
            "valid": len(errors) == 0,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings
        }


# Global exporter instance
merchant_exporter = MerchantFeedExporter()


def export_feed(
    db: Session,
    format: str = "json",
    limit: Optional[int] = None,
    category: Optional[str] = None
) -> Any:
    """
    Export product feed in specified format.
    
    Args:
        db: Database session
        format: Export format (json, xml, csv)
        limit: Maximum number of products to export
        category: Filter by category
    
    Returns:
        Feed in requested format
    """
    query = db.query(Product)
    
    if category:
        query = query.filter(Product.category == category)
    
    if limit:
        query = query.limit(limit)
    
    products = query.all()
    
    if format == "xml":
        return merchant_exporter.export_xml(products)
    elif format == "csv":
        return merchant_exporter.export_csv(products)
    else:  # json
        return merchant_exporter.export_json(products, include_metadata=True)
