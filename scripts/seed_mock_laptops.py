#!/usr/bin/env python3
"""Seed a mock 200-laptop catalog into a fresh merchant via direct DB insert.

Provisions per-merchant tables via ``create_merchant_catalog``, inserts rows
through the merchant's ORM model (same path the CSV loader uses, minus the
CSV parse), and registers the merchant in ``merchants.registry``.

Usage:
  python scripts/seed_mock_laptops.py --merchant mocklaptops --count 200

Re-seed:
  ALLOW_MERCHANT_DROP=1 python scripts/seed_mock_laptops.py \
      --merchant mocklaptops --reseed
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp-server"))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


BRANDS = [
    ("Dell", ["XPS", "Inspiron", "Latitude", "Precision", "G15"]),
    ("HP", ["Spectre", "Envy", "Pavilion", "Omen", "EliteBook"]),
    ("Lenovo", ["ThinkPad X1", "ThinkPad T", "Yoga", "Legion", "IdeaPad"]),
    ("Apple", ["MacBook Air", "MacBook Pro"]),
    ("ASUS", ["ZenBook", "ROG Strix", "TUF Gaming", "VivoBook", "ProArt"]),
    ("Acer", ["Swift", "Predator", "Aspire", "Nitro"]),
    ("Microsoft", ["Surface Laptop", "Surface Pro", "Surface Book"]),
    ("Razer", ["Blade 14", "Blade 15", "Blade 17"]),
    ("MSI", ["Stealth", "Raider", "Katana", "Modern"]),
    ("Framework", ["Laptop 13", "Laptop 16"]),
]

CPUS = [
    ("Intel Core i5-1340P", 12, 4.6),
    ("Intel Core i7-1360P", 12, 5.0),
    ("Intel Core i7-13700H", 14, 5.0),
    ("Intel Core i9-13900H", 14, 5.4),
    ("Intel Core Ultra 7 155H", 16, 4.8),
    ("AMD Ryzen 5 7640U", 6, 4.9),
    ("AMD Ryzen 7 7840HS", 8, 5.1),
    ("AMD Ryzen 9 7940HS", 8, 5.2),
    ("Apple M2", 8, None),
    ("Apple M2 Pro", 12, None),
    ("Apple M3", 8, None),
    ("Apple M3 Pro", 12, None),
    ("Apple M3 Max", 16, None),
    ("Qualcomm Snapdragon X Elite", 12, 4.0),
]

GPUS = [
    "Integrated",
    "Intel Iris Xe",
    "Intel Arc",
    "AMD Radeon 780M",
    "NVIDIA RTX 4050",
    "NVIDIA RTX 4060",
    "NVIDIA RTX 4070",
    "NVIDIA RTX 4080",
    "NVIDIA RTX 4090",
    "Apple Integrated GPU",
]

RAM_OPTIONS = [8, 16, 24, 32, 48, 64, 96, 128]
STORAGE_OPTIONS = [256, 512, 1024, 2048, 4096, 8192]
SCREEN_SIZES = [13.0, 13.3, 13.6, 14.0, 14.2, 15.6, 16.0, 16.2, 17.3, 18.0]
RESOLUTIONS = ["1920x1200", "2560x1600", "2880x1800", "3024x1964", "3456x2234", "3840x2400"]
COLORS = ["Silver", "Space Gray", "Black", "Midnight", "Platinum", "Mercury", "Eclipse"]
OSES = ["Windows 11 Home", "Windows 11 Pro", "macOS Sonoma", "Linux (Ubuntu)", "ChromeOS"]


def _gen_laptop(rng: random.Random, idx: int) -> dict:
    brand, series_pool = rng.choice(BRANDS)
    series = rng.choice(series_pool)
    is_apple = brand == "Apple"

    cpu, cores, clock = rng.choice([c for c in CPUS if (is_apple == c[0].startswith("Apple"))])
    if is_apple:
        gpu = "Apple Integrated GPU"
        os_name = "macOS Sonoma"
    else:
        # Pair gaming CPUs with discrete GPUs more often.
        if "i9" in cpu or "Ryzen 9" in cpu or "i7" in cpu:
            gpu = rng.choice(GPUS[3:])
        else:
            gpu = rng.choice(GPUS[:6])
        os_name = rng.choices(
            OSES[:2] + OSES[3:],
            weights=[5, 4, 1, 1],
            k=1,
        )[0]

    ram = rng.choice(RAM_OPTIONS)
    storage = rng.choice(STORAGE_OPTIONS)
    screen = rng.choice(SCREEN_SIZES)
    res = rng.choice(RESOLUTIONS)
    color = rng.choice(COLORS)
    weight = round(rng.uniform(2.2, 6.5), 2)
    battery = rng.randint(6, 22)
    year = rng.choice([2022, 2023, 2023, 2024, 2024, 2024])

    # Price loosely correlated with RAM, storage, GPU class.
    base = 600
    base += ram * 18
    base += storage * 0.35
    if "RTX 4090" in gpu: base += 1400
    elif "RTX 4080" in gpu: base += 900
    elif "RTX 4070" in gpu: base += 500
    elif "RTX 4060" in gpu: base += 250
    elif "RTX 4050" in gpu: base += 120
    if is_apple: base *= 1.25
    if "Pro" in series or "Precision" in series or "EliteBook" in series:
        base *= 1.15
    price = round(base * rng.uniform(0.85, 1.2), 2)

    title = f"{brand} {series} {idx:03d} {screen}\" {ram}GB RAM {storage}GB SSD"
    description = (
        f"The {brand} {series} delivers {cpu} performance with {gpu} graphics. "
        f"{ram}GB of RAM and a {storage}GB SSD on a {screen}-inch {res} display. "
        f"Up to {battery} hours of battery life. Ships in {color}."
    )

    attributes = {
        "description": description,
        "cpu": cpu,
        "cpu_cores": cores,
        "cpu_max_clock_ghz": clock,
        "gpu": gpu,
        "ram_gb": ram,
        "storage_gb": storage,
        "screen_size": screen,
        "resolution": res,
        "color": color,
        "weight_lbs": weight,
        "battery_life_hours": battery,
        "os": os_name,
        "release_year": year,
    }

    return {
        "product_id": uuid.uuid4(),
        "name": title,
        "category": "electronics",
        "product_type": "laptop",
        "brand": brand,
        "series": series,
        "price_value": price,
        "image_url": f"https://example.com/mock/laptops/{brand.lower()}/{idx:03d}.jpg",
        "rating": round(rng.uniform(3.4, 4.9), 1),
        "rating_count": rng.randint(5, 4200),
        "source": "mock:seed_mock_laptops",
        "link": f"https://example.com/mock/laptops/{idx:03d}",
        "ref_id": f"MOCK-LAPTOP-{idx:04d}",
        "release_year": year,
        "attributes": attributes,
    }


def _maybe_drop_existing(merchant_id: str, engine) -> None:
    from app.ingestion.schema import drop_merchant_catalog
    raw_conn = engine.raw_connection()
    try:
        drop_merchant_catalog(merchant_id, raw_conn)
    finally:
        raw_conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Seed a mock laptop catalog (direct DB insert).")
    p.add_argument("--merchant", default="mocklaptops",
                   help="Merchant slug. Must match [a-z][a-z0-9_]{1,31}.")
    p.add_argument("--count", type=int, default=200, help="Number of laptops (default 200).")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility.")
    p.add_argument("--reseed", action="store_true",
                   help="Drop the merchant catalog first (requires ALLOW_MERCHANT_DROP=1).")
    p.add_argument("--domain", default="electronics")
    p.add_argument("--strategy", default="normalizer_v1")
    p.add_argument("--kg-strategy", default="default_v1")
    args = p.parse_args()

    from app.database import SessionLocal
    from app.ingestion.schema import create_merchant_catalog
    from app.merchant_agent import (
        MerchantAgent,
        upsert_registry_row,
        validate_merchant_id,
    )

    merchant_id = validate_merchant_id(args.merchant)

    session = SessionLocal()
    engine = session.get_bind()
    session.close()

    if args.reseed:
        if os.environ.get("ALLOW_MERCHANT_DROP") != "1":
            print("--reseed requires ALLOW_MERCHANT_DROP=1", file=sys.stderr)
            return 2
        _maybe_drop_existing(merchant_id, engine)

    raw_conn = engine.raw_connection()
    try:
        create_merchant_catalog(merchant_id, raw_conn)
    finally:
        raw_conn.close()

    agent = MerchantAgent(
        merchant_id=merchant_id,
        domain=args.domain,
        strategy=args.strategy,
        kg_strategy=args.kg_strategy,
    )
    Product = agent.product_model

    db = SessionLocal()
    try:
        existing = db.query(Product).limit(1).count()
        if existing:
            print(
                f"merchant {merchant_id!r} already has rows; pass --reseed "
                f"with ALLOW_MERCHANT_DROP=1 to wipe and re-insert.",
                file=sys.stderr,
            )
            return 2

        rng = random.Random(args.seed)
        rows = [_gen_laptop(rng, i + 1) for i in range(args.count)]
        for row in rows:
            db.add(Product(merchant_id=merchant_id, **row))
        db.commit()
    finally:
        db.close()

    upsert_registry_row(
        merchant_id=merchant_id,
        domain=args.domain,
        strategy=args.strategy,
        kg_strategy=args.kg_strategy,
    )

    print(json.dumps({
        "merchant_id": merchant_id,
        "domain": args.domain,
        "strategy": args.strategy,
        "kg_strategy": args.kg_strategy,
        "catalog_table": agent.catalog_table(),
        "enriched_table": agent.enriched_table(),
        "inserted": args.count,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
