import unittest
import sys
import os

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))

from app.formatters import format_product
from app.schemas import ProductType

class TestFormatter(unittest.TestCase):
    def test_format_vehicle_nested(self):
        # IDSS vehicle structure
        raw = {
            "@id": "vin123",
            "vehicle": {
                "year": 2024,
                "make": "Honda",
                "model": "Civic",
                "price": 25000,
                "bodyStyle": "Sedan"
            },
            "retailListing": {
                "primaryImage": "http://img.com/car.jpg",
                "photoCount": 5
            }
        }
        unified = format_product(raw, "vehicles")
        
        self.assertEqual(unified.productType, ProductType.VEHICLE)
        self.assertEqual(unified.id, "vin123")
        self.assertEqual(unified.vehicle.make, "Honda")
        self.assertEqual(unified.price, 25000)
        self.assertEqual(unified.image.primary, "http://img.com/car.jpg")
        
    def test_format_laptop(self):
        # Ecommerce structure (flat)
        raw = {
            "product_id": "lap456",
            "name": "Gaming Laptop X",
            "category": "Electronics",
            "price": 1500,
            "gpu_model": "RTX 4060",
            "ram": "16GB" # Not in current DB model but let's test if passed
        }
        unified = format_product(raw, "laptops")
        
        self.assertEqual(unified.productType, ProductType.LAPTOP)
        self.assertEqual(unified.id, "lap456")
        self.assertEqual(unified.laptop.specs.graphics, "RTX 4060")
        
    def test_format_book(self):
        raw = {
            "product_id": "bk789",
            "name": "Python 101",
            "category": "Books",
            "price": 50,
            "author": "Guido"
        }
        unified = format_product(raw, "books")
        
        self.assertEqual(unified.productType, ProductType.BOOK)
        self.assertEqual(unified.book.author, "Guido")

if __name__ == '__main__':
    unittest.main()
