#!/usr/bin/env python3
"""Quick verification of Shopify products in database."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price

db = SessionLocal()

shopify_products = db.query(Product).filter(Product.source == "Shopify").limit(10).all()

print(f"\n Found {len(shopify_products)} Shopify products in database:\n")

for p in shopify_products:
    price_obj = db.query(Price).filter(Price.product_id == p.product_id).first()
    price = price_obj.price_cents / 100 if price_obj else 0
    print(f"  • {p.name[:50]:<50} ${price:.2f}")

db.close()
