#!/usr/bin/env python3
"""
Check MCP e-commerce database: product count, categories, and schema.
Use this to see why you get "no X match criteria" (often empty DB or wrong DATABASE_URL).

Run from repo root:
  cd idss-mcp/mcp-server && python scripts/check_db_products.py

Or with explicit DB:
  DATABASE_URL=postgresql://user:pass@localhost:5432/mcp_ecommerce python scripts/check_db_products.py
"""

import os
import sys
from pathlib import Path

# Add parent so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

def main():
    from sqlalchemy import create_engine, text
    from app.database import DATABASE_URL

    # Mask password in URL for display
    display_url = DATABASE_URL
    if "@" in DATABASE_URL and ":" in DATABASE_URL.split("@")[0]:
        try:
            user_part = DATABASE_URL.split("@")[0]
            if "://" in user_part:
                scheme, rest = user_part.split("://", 1)
                display_url = f"{scheme}://***@{DATABASE_URL.split('@')[1]}"
        except Exception:
            pass

    print("=== MCP E-commerce DB Check ===\n")
    print(f"DATABASE_URL: {display_url}")
    print()

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            # Check if products table exists
            r = conn.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'products'
                )
            """))
            if not r.scalar():
                print("[FAIL] Table 'products' does not exist.")
                print("   Run: psql mcp_ecommerce -f scripts/seed.sql")
                print("   Then: psql mcp_ecommerce -f scripts/add_subcategory_column.sql")
                print("        psql mcp_ecommerce -f scripts/add_source_column.sql")
                print("        psql mcp_ecommerce -f scripts/add_color_and_scraped_from.sql")
                return 1

            # Count products
            total = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
            print(f"Total products: {total}")

            if total == 0:
                print("\n[WARN] Database has 0 products - that's why you see 'no X match criteria'.")
                print("\nTo populate with real/scraped data:")
                print("  1. Ensure migrations (subcategory, source, color, scraped_from_url) are applied.")
                print("  2. Run one of:")
                print("     python scripts/populate_real_products.py --clear   # seed-only (laptops + books)")
                print("     python scripts/populate_real_products.py --scrape-macs --clear   # scrape + seed")
                print("     python scripts/scrape_barnes_noble_books.py   # books from B&N")
                print("  From repo root you can also run: ./scripts/setup_and_populate.sh")
                return 0

            # Count by category (column might not exist in very old schema)
            try:
                r = conn.execute(text("""
                    SELECT category, COUNT(*) AS cnt
                    FROM products
                    GROUP BY category
                    ORDER BY cnt DESC
                """))
                rows = r.fetchall()
                print("\nBy category:")
                for row in rows:
                    print(f"  {row[0] or '(NULL)'}: {row[1]}")
            except Exception as e:
                if "column" in str(e).lower() and "does not exist" in str(e).lower():
                    print("\n[WARN] 'category' column missing. Run add_subcategory_column.sql and add_source_column.sql, add_color_and_scraped_from.sql.")
                else:
                    print(f"\nCategory breakdown failed: {e}")

            # Check expected columns for search/filters
            r = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                ORDER BY ordinal_position
            """))
            cols = {row[0] for row in r.fetchall()}
            expected = {"category", "subcategory", "source", "scraped_from_url", "color"}
            missing = expected - cols
            if missing:
                print(f"\n[WARN] Missing columns (search may be limited): {missing}")
                print("   Run: psql mcp_ecommerce -f scripts/add_subcategory_column.sql")
                print("        psql mcp_ecommerce -f scripts/add_source_column.sql")
                print("        psql mcp_ecommerce -f scripts/add_color_and_scraped_from.sql")
            else:
                print("\n[OK] Schema has category, subcategory, source, scraped_from_url, color.")

            # --- Catalog truth checks (prove why "gaming PC" / "pink laptop" return 0 or wrong results) ---
            print("\n--- Catalog truth checks ---")
            try:
                r = conn.execute(text("""
                    SELECT product_type, COUNT(*) AS cnt
                    FROM products
                    GROUP BY product_type
                    ORDER BY cnt DESC
                """))
                rows = r.fetchall()
                print("product_type values:")
                for row in rows:
                    print(f"  {row[0] or '(NULL)'}: {row[1]}")
            except Exception as e:
                if "column" in str(e).lower():
                    print("  (product_type column missing)")
                else:
                    print(f"  product_type: {e}")

            try:
                r = conn.execute(text("""
                    SELECT gpu_vendor, COUNT(*) AS cnt
                    FROM products
                    GROUP BY gpu_vendor
                    ORDER BY cnt DESC
                """))
                rows = r.fetchall()
                print("\ngpu_vendor values:")
                for row in rows:
                    print(f"  {row[0] or '(NULL)'}: {row[1]}")
            except Exception as e:
                if "column" in str(e).lower():
                    print("  (gpu_vendor column missing)")
                else:
                    print(f"  gpu_vendor: {e}")

            try:
                r = conn.execute(text("""
                    SELECT subcategory, COUNT(*) AS cnt
                    FROM products
                    WHERE category = 'Electronics'
                    GROUP BY subcategory
                    ORDER BY cnt DESC
                """))
                rows = r.fetchall()
                print("\nElectronics subcategory (use_case) values:")
                for row in rows:
                    print(f"  {row[0] or '(NULL)'}: {row[1]}")
            except Exception as e:
                print(f"  subcategory: {e}")

            try:
                r = conn.execute(text("""
                    SELECT COUNT(*) FROM products
                    WHERE category = 'Electronics'
                      AND (product_type IN ('desktop_pc','gaming_laptop') OR product_type IS NULL)
                      AND (gpu_vendor ILIKE '%nvidia%' OR gpu_vendor IS NULL)
                      AND product_id IN (SELECT product_id FROM prices WHERE price_cents <= 200000)
                """))
                n = r.scalar()
                print(f"\nElectronics + (desktop_pc|gaming_laptop) + NVIDIA-ish + price<=$2000: {n}")
            except Exception as e:
                print(f"  (query failed: {e})")

            try:
                r = conn.execute(text("SELECT COUNT(*) FROM products WHERE category = 'Books'"))
                books = r.scalar()
                print(f"\nBooks total: {books}")
                if books and books > 0:
                    # Price ranges (cents): $15=$1500, $30=$3000, $50=$5000
                    for label, min_c, max_c in [
                        ("Books $15-$30 (1500-3000 cents)", 1500, 3000),
                        ("Books under $15 (<1500)", 0, 1499),
                        ("Books $30-$50 (3001-5000)", 3001, 5000),
                        ("Books over $50 (>5000)", 5001, 99999999),
                    ]:
                        r2 = conn.execute(
                            text("""
                                SELECT COUNT(*) FROM products p
                                JOIN prices pr ON p.product_id = pr.product_id
                                WHERE p.category = 'Books' AND pr.price_cents >= :lo AND pr.price_cents <= :hi
                            """),
                            {"lo": min_c, "hi": max_c},
                        )
                        n = r2.scalar()
                        print(f"  {label}: {n}")
            except Exception as e:
                print(f"  Books count: {e}")

            try:
                r = conn.execute(text("""
                    SELECT color, COUNT(*) AS cnt FROM products
                    WHERE category = 'Electronics'
                    GROUP BY color ORDER BY cnt DESC
                """))
                rows = r.fetchall()
                print("\nElectronics color values:")
                for row in rows:
                    print(f"  {row[0] or '(NULL)'}: {row[1]}")
            except Exception as e:
                print(f"  color: {e}")

            # Sample product names
            r = conn.execute(text("SELECT name, category FROM products LIMIT 5"))
            print("\nSample products:")
            for row in r.fetchall():
                print(f"  {row[1] or '?'}: {row[0][:50]}")

    except Exception as e:
        print(f"\n[FAIL] Database error: {e}")
        print("   Check: PostgreSQL running? DATABASE_URL correct? Database 'mcp_ecommerce' created?")
        print("   Example: createdb mcp_ecommerce")
        return 1

    print("\n=== Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
