#!/usr/bin/env python3
"""
Export product catalog as a versioned JSON snapshot.

This script demonstrates JSON as an artifact approach:
- JSON is NOT the source of truth (Postgres is)
- JSON snapshots are versioned and validated
- Atomic swap ensures no partial/corrupt snapshots
- Catalog version tracking enables staleness detection

Usage:
    python scripts/export_snapshot.py

Output:
    snapshots/catalog_v{version}.json (versioned snapshot)
    snapshots/catalog_current -> catalog_v{version}.json (symbolic link)
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app.models import Product, Price, Inventory


# Catalog schema version - increment when snapshot format changes
SCHEMA_VERSION = "1.0.0"

# Output directory for snapshots
SNAPSHOT_DIR = Path(__file__).parent.parent / "snapshots"


def generate_catalog_version() -> str:
    """
    Generate a new catalog version identifier.
    Uses timestamp-based versioning for simplicity.
    In production, could use git commit hash or semantic versioning.
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"v{timestamp}"


def validate_snapshot(snapshot_data: dict) -> bool:
    """
    Validate snapshot before publishing.
    
    Ensures:
    - Required fields are present
    - Schema version is valid
    - Products list is not empty
    - Each product has required attributes
    
    Returns True if valid, False otherwise.
    """
    # Check top-level structure
    required_fields = ["schema_version", "catalog_version", "generated_at", "products"]
    for field in required_fields:
        if field not in snapshot_data:
            print(f"Validation error: Missing required field '{field}'")
            return False
    
    # Check schema version format
    if not snapshot_data["schema_version"]:
        print("Validation error: Empty schema_version")
        return False
    
    # Check products list
    if not isinstance(snapshot_data["products"], list):
        print("Validation error: 'products' must be a list")
        return False
    
    if len(snapshot_data["products"]) == 0:
        print("Validation error: Empty products list")
        return False
    
    # Validate each product has required fields
    required_product_fields = ["product_id", "name", "price_cents", "currency", "available_qty"]
    for idx, product in enumerate(snapshot_data["products"]):
        for field in required_product_fields:
            if field not in product:
                print(f"Validation error: Product {idx} missing field '{field}'")
                return False
    
    print(f"Validation passed: {len(snapshot_data['products'])} products")
    return True


def export_catalog_snapshot(db: Session) -> str:
    """
    Export current product catalog from Postgres to a versioned JSON snapshot.
    
    Process:
    1. Query all products with price and inventory from Postgres
    2. Build JSON structure with versioning metadata
    3. Validate the snapshot
    4. Write to temporary file
    5. Atomic swap (rename) to final location
    6. Update "current" pointer
    
    Returns: Path to the created snapshot file
    """
    print("Starting catalog snapshot export...")
    
    # Query all products with related data
    print("Querying products from Postgres...")
    products = db.query(Product).join(Price).join(Inventory).all()
    print(f"Found {len(products)} products")
    
    # Build snapshot structure
    catalog_version = generate_catalog_version()
    
    snapshot_data = {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": catalog_version,
        "generated_at": datetime.utcnow().isoformat(),
        "products": []
    }
    
    # Export each product
    for product in products:
        product_data = {
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "brand": product.brand,
            "price_cents": product.price_info.price_cents,
            "currency": product.price_info.currency,
            "available_qty": product.inventory_info.available_qty,
            "created_at": product.created_at.isoformat(),
            "updated_at": product.updated_at.isoformat()
        }
        snapshot_data["products"].append(product_data)
    
    # Validate snapshot before publishing
    print("Validating snapshot...")
    if not validate_snapshot(snapshot_data):
        raise ValueError("Snapshot validation failed")
    
    # Ensure snapshot directory exists
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    
    # Define file paths
    snapshot_filename = f"catalog_{catalog_version}.json"
    snapshot_path = SNAPSHOT_DIR / snapshot_filename
    temp_path = SNAPSHOT_DIR / f"{snapshot_filename}.tmp"
    current_link = SNAPSHOT_DIR / "catalog_current.json"
    
    # Write to temporary file first (atomic swap pattern)
    print(f"Writing snapshot to temporary file: {temp_path}")
    with open(temp_path, 'w') as f:
        json.dump(snapshot_data, f, indent=2)
    
    # Atomic rename - this ensures no partial/corrupt files
    print(f"Performing atomic swap to: {snapshot_path}")
    os.rename(temp_path, snapshot_path)
    
    # Update "current" pointer
    # Remove old symlink if it exists
    if current_link.exists() or current_link.is_symlink():
        current_link.unlink()
    
    # Create new symlink to current snapshot
    # Use relative path for portability
    current_link.symlink_to(snapshot_filename)
    
    print(f"[OK] Snapshot exported successfully:")
    print(f"  Version: {catalog_version}")
    print(f"  File: {snapshot_path}")
    print(f"  Products: {len(snapshot_data['products'])}")
    print(f"  Current pointer: {current_link} -> {snapshot_filename}")
    
    return str(snapshot_path)


def rollback_to_version(version: str):
    """
    Rollback to a previous snapshot version.
    
    This demonstrates how snapshots enable rollback:
    - Keep last N snapshots
    - Update "current" pointer to previous version
    
    Args:
        version: Version string (e.g., "v20240119_120000")
    """
    snapshot_filename = f"catalog_{version}.json"
    snapshot_path = SNAPSHOT_DIR / snapshot_filename
    current_link = SNAPSHOT_DIR / "catalog_current.json"
    
    if not snapshot_path.exists():
        print(f"Error: Snapshot version {version} not found")
        return False
    
    # Update current pointer
    if current_link.exists() or current_link.is_symlink():
        current_link.unlink()
    
    current_link.symlink_to(snapshot_filename)
    
    print(f"[OK] Rolled back to version {version}")
    print(f"  Current pointer: {current_link} -> {snapshot_filename}")
    
    return True


def list_snapshots():
    """
    List all available catalog snapshots.
    Useful for finding versions to rollback to.
    """
    if not SNAPSHOT_DIR.exists():
        print("No snapshots directory found")
        return
    
    snapshots = sorted(SNAPSHOT_DIR.glob("catalog_v*.json"))
    
    if not snapshots:
        print("No snapshots found")
        return
    
    print(f"Available snapshots ({len(snapshots)}):")
    for snapshot in snapshots:
        # Get file size and modification time
        stat = snapshot.stat()
        size_kb = stat.st_size / 1024
        mtime = datetime.fromtimestamp(stat.st_mtime)
        
        # Check if this is the current version
        current_link = SNAPSHOT_DIR / "catalog_current.json"
        is_current = current_link.exists() and current_link.resolve() == snapshot
        
        current_marker = " [CURRENT]" if is_current else ""
        print(f"  {snapshot.name} - {size_kb:.1f} KB - {mtime.strftime('%Y-%m-%d %H:%M:%S')}{current_marker}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage catalog snapshots")
    parser.add_argument("--export", action="store_true", help="Export current catalog to snapshot")
    parser.add_argument("--list", action="store_true", help="List all available snapshots")
    parser.add_argument("--rollback", metavar="VERSION", help="Rollback to specific version")
    
    args = parser.parse_args()
    
    # Default behavior: export
    if not any([args.export, args.list, args.rollback]):
        args.export = True
    
    if args.list:
        list_snapshots()
    
    elif args.rollback:
        rollback_to_version(args.rollback)
    
    elif args.export:
        # Create database session
        db = SessionLocal()
        try:
            export_catalog_snapshot(db)
        finally:
            db.close()
