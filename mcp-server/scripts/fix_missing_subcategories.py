#!/usr/bin/env python3
"""
Fix Missing Subcategories for Laptops

Updates all laptop products to have proper subcategories based on their names/descriptions.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product


def infer_subcategory(product):
    """Infer laptop subcategory from name and description."""
    name = product.name.lower() if product.name else ""
    desc = product.description.lower() if product.description else ""
    combined = f"{name} {desc}"
    
    # Gaming indicators
    gaming_keywords = ["gaming", "rog", "strix", "omen", "predator", "legion", "alienware", 
                       "tuf", "katana", "helios", "rtx", "aorus", "blade", "scar"]
    if any(kw in combined for kw in gaming_keywords):
        return "Gaming"
    
    # Creative/Workstation indicators
    creative_keywords = ["pro", "studio", "creator", "zbook", "precision", "macbook pro",
                         "xps 17", "dell precision", "thinkpad p", "quadro"]
    if any(kw in combined for kw in creative_keywords):
        return "Creative"
    
    # Work/Business indicators
    work_keywords = ["thinkpad", "latitude", "elitebook", "probook", "business", 
                     "vostro", "travelmate", "envy"]
    if any(kw in combined for kw in work_keywords):
        return "Work"
    
    # School/Budget indicators
    school_keywords = ["ideapad", "aspire", "vivobook", "inspiron", "chromebook", 
                       "student", "education", "basic"]
    if any(kw in combined for kw in school_keywords):
        return "School"
    
    # Check price if available (via price table join would be complex, use simple heuristic)
    if "air" in combined or "slim" in combined or "ultrabook" in combined:
        return "Work"
    
    # Default
    return "General"


def main():
    """Update all laptops missing subcategories."""
    print("="*80)
    print("FIXING MISSING LAPTOP SUBCATEGORIES")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        # Get all Electronics products (laptops)
        laptops = db.query(Product).filter(Product.category == "Electronics").all()
        
        print(f"\nFound {len(laptops)} laptop products")
        
        # Check how many are missing subcategories
        missing = [l for l in laptops if not l.subcategory]
        print(f"Missing subcategories: {len(missing)}")
        
        if missing:
            print("\nUpdating products...")
            updated = 0
            
            for laptop in missing:
                inferred = infer_subcategory(laptop)
                laptop.subcategory = inferred
                updated += 1
                
                if updated <= 5:  # Show first 5 examples
                    print(f"  ✓ {laptop.name[:40]:<40} → {inferred}")
            
            db.commit()
            print(f"\n Updated {updated} laptop subcategories")
        else:
            print("\n All laptops already have subcategories!")
        
        # Show distribution
        print("\nSubcategory Distribution:")
        from sqlalchemy import func
        subcat_counts = db.query(
            Product.subcategory,
            func.count(Product.product_id)
        ).filter(
            Product.category == "Electronics"
        ).group_by(
            Product.subcategory
        ).all()
        
        for subcat, count in subcat_counts:
            print(f"  {subcat or '(None)'}: {count}")
        
    finally:
        db.close()
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()
