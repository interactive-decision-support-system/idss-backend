#!/usr/bin/env python3
"""
Backfill kg_features (PostgreSQL) for richer KG (§7).

Sets Reddit-style features from existing product data (per week6tips: 30+ features).
Features inferred:
- good_for_ml, good_for_gaming, good_for_web_dev, good_for_creative
- good_for_linux, good_for_programming, repairable, refurbished
- battery_life_hours, ram_gb, storage_gb, screen_size_inches, year
- keyboard_quality (tiered: excellent/good based on context)

Run from mcp-server: python scripts/backfill_kg_features.py
"""
import os
import re
import sys

# Add parent so app is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Product


def parse_battery_hours(description: str | None, name: str | None) -> int | None:
    """Try to extract battery life in hours from text. Returns None if not found."""
    text = f" {(description or '')} {(name or '')} ".lower()
    # e.g. "10 hour battery", "up to 12 hours", "8hr"
    m = re.search(r"(?:up to |about |approx\.? )?(\d+)\s*(?:hour|hr)s?", text)
    if m:
        return int(m.group(1))
    m = re.search(r"battery[^\d]*(\d+)\s*(?:hour|hr)", text)
    if m:
        return int(m.group(1))
    return None


def parse_ram_gb(text: str) -> int | None:
    """Extract RAM in GB from text. e.g. '16GB RAM', '32 GB', '64gb'."""
    m = re.search(r"(\d+)\s*(?:gb|gigabytes?)\s*(?:ram|ddr|memory)?", text, re.I)
    if m:
        gb = int(m.group(1))
        if 4 <= gb <= 256:
            return gb
    return None


def parse_storage_gb(text: str) -> int | None:
    """Extract storage in GB from text. e.g. '512GB SSD', '1TB', '256 GB'."""
    m = re.search(r"(\d+)\s*(?:tb|terabytes?)", text, re.I)
    if m:
        return int(m.group(1)) * 1024
    m = re.search(r"(\d+)\s*(?:gb|gigabytes?)\s*(?:ssd|nvme|storage)?", text, re.I)
    if m:
        gb = int(m.group(1))
        if 64 <= gb <= 8192:
            return gb
    return None


def parse_screen_inches(text: str) -> float | None:
    """Extract screen size in inches. e.g. '15.6"', '16 inch', '14 inch'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?: inch|in\.?|[\"'])", text, re.I)
    if m:
        n = float(m.group(1))
        if 10 <= n <= 24:
            return n
    m = re.search(r"(\d+(?:\.\d+)?)\s*inch", text, re.I)
    if m:
        return float(m.group(1))
    return None


def parse_year(text: str) -> int | None:
    """Extract product/model year. e.g. '2024 model', 'Laptop (2023)', 'MacBook Pro 2024'."""
    # Pattern: standalone year like "(2024)", "2023 laptop", "laptop 2024"
    m = re.search(r"\b(20[12]\d)\b", text)
    if m:
        year = int(m.group(1))
        if 2015 <= year <= 2026:
            return year
    return None


def build_kg_features(product: Product) -> dict | None:
    """Build kg_features dict for a product from existing fields (30+ features per week6tips)."""
    features: dict = {}
    name = (product.name or "").lower()
    desc = (product.description or "").lower()
    sub = (product.subcategory or "").lower()
    tags = product.tags or []
    tag_str = " ".join(t.lower() for t in tags if isinstance(t, str))
    combined = f" {name} {desc} {tag_str} {sub} "
    source = (product.source or "").lower()
    scraped_url = (product.scraped_from_url or "").lower()

    # good_for_gaming
    if "gaming" in sub or "gaming" in combined or "gaming_laptop" == (product.product_type or ""):
        features["good_for_gaming"] = True
    # good_for_ml: NVIDIA/AMD GPU, 32GB+ RAM, ML/AI keywords, CUDA, data science
    if any(x in combined for x in [
        "nvidia", "rtx", "gtx", "32gb", "64gb", "128gb",
        "machine learning", "deep learning", "ml ", " ai ",
        "cuda", "tensor", "data science", "jupyter", "pytorch", "tensorflow",
        "instinct", "intel arc", "a100", "h100", "v100",
    ]):
        features["good_for_ml"] = True
    # good_for_web_dev: actual web dev tools/frameworks (tightened from broad "work")
    if any(x in combined for x in [
        "web dev", "web development", "frontend", "fullstack", "full-stack",
        "react", "node", "angular", "vue", "figma", "webflow", "xano",
    ]) or "work" in sub or "school" in sub:
        features["good_for_web_dev"] = True
    # good_for_programming: general coding/IDE/language mentions
    if any(x in combined for x in [
        "programming", "developer", "coding", "software development",
        "ide", "compiler", "debug", "python", "java ", "c++", "rust",
        "vscode", "pycharm", "intellij", "vim", "emacs",
    ]):
        features["good_for_programming"] = True
    # good_for_creative: video, photo, creative, 3D
    if "creative" in sub or any(x in combined for x in [
        "video", "photo", "editing", "3d modeling", "blender",
        "photoshop", "premiere", "davinci", "autocad",
    ]):
        features["good_for_creative"] = True

    # good_for_linux: System76, Framework, Pop!_OS, Linux
    if "system76" in source or "framework" in source or "linux" in combined or "pop!_os" in combined or "pop os" in combined:
        features["good_for_linux"] = True

    # repairable: Framework, Fairphone
    if "framework" in source or "fairphone" in source or "repairable" in combined or "modular" in combined:
        features["repairable"] = True

    # refurbished: Back Market, refurbished in text
    if "back market" in source or "backmarket" in source or "refurbished" in combined:
        features["refurbished"] = True

    # keyboard_quality: tiered based on context
    if any(x in combined for x in ["thinkpad", "mechanical keyboard", "travel keyboard", "excellent keyboard"]):
        features["keyboard_quality"] = "excellent"
    elif "keyboard" in combined or "typing" in combined or "backlit" in combined:
        features["keyboard_quality"] = "good"

    # battery_life_hours
    hours = parse_battery_hours(product.description, product.name)
    if hours is not None:
        features["battery_life_hours"] = hours
    elif product.category == "Electronics" and (product.product_type or "").lower() in ("laptop", "gaming_laptop", ""):
        features["battery_life_hours"] = 8

    # ram_gb, storage_gb, screen_size_inches (parse from text)
    ram = parse_ram_gb(combined)
    if ram is not None:
        features["ram_gb"] = ram
    storage = parse_storage_gb(combined)
    if storage is not None:
        features["storage_gb"] = storage
    screen = parse_screen_inches(combined)
    if screen is not None:
        features["screen_size_inches"] = screen

    # year (model/release year)
    year = parse_year(combined)
    if year is not None:
        features["year"] = year

    # Phones: battery_life_hours fallback
    if product.category == "Electronics" and (product.product_type or "").lower() in ("phone", "smartphone"):
        if "battery_life_hours" not in features:
            features["battery_life_hours"] = 12  # typical phone

    if not features:
        return None
    return features


def main():
    db = SessionLocal()
    try:
        products = db.query(Product).all()
    except Exception as e:
        if "kg_features" in str(e) or "UndefinedColumn" in str(e):
            print("The products.kg_features column is missing. Run the migration first:")
            print("  PostgreSQL: psql <your_db> -f scripts/add_kg_features_column.sql")
            print("  Or ensure tables are created from the current models (e.g. create_all).")
        else:
            raise
        return
    try:
        updated = 0
        for p in products:
            kg = build_kg_features(p)
            if kg:
                p.kg_features = kg
                updated += 1
        db.commit()
        print(f"Backfilled kg_features for {updated} / {len(products)} products.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
