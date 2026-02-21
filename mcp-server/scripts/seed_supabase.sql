-- =============================================================================
-- IDSS Supabase-Compatible Seed Script
-- =============================================================================
-- Creates the products table matching the live Supabase schema and seeds
-- sample data for local development.
--
-- Usage:
--   psql $DATABASE_URL -f scripts/seed_supabase.sql
--   -- OR from Python:
--   python scripts/seed_supabase_local.py
--
-- Schema: Supabase 'products' table (single-table design)
--   - Price stored directly on products (decimal dollars, NOT cents)
--   - No separate prices/inventory/carts/orders tables
--   - Specs stored in 'attributes' JSONB column
--   - 'id' is UUID (auto-generated), 'title' maps to product name
--   - 'imageurl' (no underscore) for product images
-- =============================================================================

-- Enable UUID extension (Supabase has this; local PG may not)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Drop and recreate products table
DROP TABLE IF EXISTS products CASCADE;

CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    price           NUMERIC,
    brand           TEXT,
    series          TEXT,
    model           TEXT,
    title           TEXT,
    link            TEXT,
    imageurl        TEXT,
    rating          NUMERIC,
    rating_count    BIGINT,
    source          TEXT,
    attributes      JSONB,
    product_type    TEXT,
    category        TEXT,
    ref_id          TEXT,
    variant         TEXT,
    inventory       BIGINT DEFAULT floor(random() * 106 + 15),
    release_year    SMALLINT,
    delivery_promise    TEXT,
    return_policy       TEXT,
    warranty            TEXT,
    promotions_discounts TEXT,
    merchant_product_url TEXT,
    enriched_at         TIMESTAMPTZ,
    enrichment_steps_done TEXT[]
);

-- Indexes (match Supabase)
CREATE INDEX idx_products_title ON products(title);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_products_product_type ON products(product_type);
CREATE INDEX idx_products_source ON products(source);
CREATE INDEX idx_products_price ON products(price);
CREATE INDEX idx_products_rating ON products(rating);

-- =============================================================================
-- SAMPLE LAPTOPS (Electronics)
-- =============================================================================
INSERT INTO products (title, brand, price, category, product_type, source, rating, rating_count, imageurl, model, attributes) VALUES

-- Apple
('Apple MacBook Pro 16" M3 Max 48GB 1TB', 'Apple', 3499.00, 'Electronics', 'laptop', 'Apple', 4.8, 2340,
 'https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/mbp16-spacegray-select-202310',
 'MacBook Pro 16',
 '{"ram_gb": 48, "storage_gb": 1024, "cpu": "Apple M3 Max", "gpu_vendor": "Apple", "gpu_model": "M3 Max GPU", "screen_size": 16.2, "battery_hours": 22, "os": "macOS", "color": "Space Black", "description": "Apple MacBook Pro 16-inch with M3 Max chip, 48GB unified memory, 1TB SSD"}'),

('Apple MacBook Air 15" M3 16GB 512GB', 'Apple', 1299.00, 'Electronics', 'laptop', 'Apple', 4.7, 1856,
 'https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/mba15-midnight-select-202306',
 'MacBook Air 15',
 '{"ram_gb": 16, "storage_gb": 512, "cpu": "Apple M3", "gpu_vendor": "Apple", "gpu_model": "M3 GPU", "screen_size": 15.3, "battery_hours": 18, "os": "macOS", "color": "Midnight", "description": "Apple MacBook Air 15-inch with M3 chip"}'),

-- Dell
('Dell XPS 15 OLED Intel i9 32GB 1TB', 'Dell', 2199.00, 'Electronics', 'laptop', 'Dell', 4.5, 890,
 'https://i.dell.com/is/image/DellContent/content/dam/ss2/product-images/dell-client-products/notebooks/xps-notebooks/xps-15-9530/media-gallery/touch/notebook-xps-15-9530-t-media-gallery-1.psd',
 'XPS 15 9530',
 '{"ram_gb": 32, "storage_gb": 1024, "cpu": "Intel Core i9-13900H", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4060", "screen_size": 15.6, "battery_hours": 13, "os": "Windows 11", "color": "Platinum Silver", "description": "Dell XPS 15 with 3.5K OLED display, i9 processor, RTX 4060"}'),

-- Lenovo
('Lenovo ThinkPad X1 Carbon Gen 11 i7 16GB', 'Lenovo', 1649.00, 'Electronics', 'laptop', 'Lenovo', 4.6, 1120,
 'https://p1-ofp.static.pub/fes/cms/2023/01/05/e0x0jmwf0b1epbvbrjgh5c9xrqgp1p594753.png',
 'ThinkPad X1 Carbon Gen 11',
 '{"ram_gb": 16, "storage_gb": 512, "cpu": "Intel Core i7-1365U", "screen_size": 14.0, "battery_hours": 15, "os": "Windows 11 Pro", "color": "Black", "description": "Lenovo ThinkPad X1 Carbon Gen 11 - ultralight business laptop"}'),

-- ASUS Gaming
('ASUS ROG Zephyrus G16 RTX 4070 32GB', 'ASUS', 1899.00, 'Electronics', 'gaming_laptop', 'ASUS', 4.4, 670,
 'https://dlcdnwebimgs.asus.com/gain/8A4B2A37-3E5A-4C60-A50D-9B33AACF5F37/w717/h525',
 'ROG Zephyrus G16',
 '{"ram_gb": 32, "storage_gb": 1024, "cpu": "Intel Core i9-14900HX", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4070", "screen_size": 16.0, "battery_hours": 10, "os": "Windows 11", "color": "Eclipse Gray", "description": "ASUS ROG Zephyrus G16 gaming laptop with RTX 4070", "good_for_gaming": true}'),

-- System76
('System76 Lemur Pro 14" Ryzen 7 32GB', 'System76', 1299.00, 'Electronics', 'laptop', 'System76', 4.3, 89,
 'https://system76.com/imgs/laptops/lemp13/lemp13-front.png',
 'Lemur Pro',
 '{"ram_gb": 32, "storage_gb": 1024, "cpu": "AMD Ryzen 7 7840U", "screen_size": 14.0, "battery_hours": 14, "os": "Pop!_OS / Ubuntu", "color": "Silver", "description": "System76 Lemur Pro - lightweight Linux laptop with all-day battery", "good_for_ml": true, "good_for_web_dev": true}'),

-- Framework
('Framework Laptop 16 AMD Ryzen 9 64GB', 'Framework', 2049.00, 'Electronics', 'laptop', 'Framework', 4.5, 234,
 'https://frame.work/media/Framework_Laptop_16_Front.png',
 'Framework 16',
 '{"ram_gb": 64, "storage_gb": 2048, "cpu": "AMD Ryzen 9 7940HS", "gpu_vendor": "AMD", "gpu_model": "Radeon RX 7700S", "screen_size": 16.0, "battery_hours": 10, "os": "Linux / Windows", "color": "Silver", "description": "Framework Laptop 16 - modular, repairable, upgradeable", "good_for_ml": true}'),

-- HP
('HP Spectre x360 14 OLED i7 16GB', 'HP', 1499.00, 'Electronics', 'laptop', 'HP', 4.5, 560,
 'https://ssl-product-images.www8-hp.com/digmedialib/prodimg/lowres/c08911015.png',
 'Spectre x360 14',
 '{"ram_gb": 16, "storage_gb": 1024, "cpu": "Intel Core i7-1355U", "screen_size": 13.5, "battery_hours": 16, "os": "Windows 11", "color": "Nightfall Black", "description": "HP Spectre x360 14 - premium 2-in-1 convertible with OLED touch display"}'),

-- MSI Gaming
('MSI Raider GE78 HX RTX 4090 64GB', 'MSI', 3299.00, 'Electronics', 'gaming_laptop', 'MSI', 4.6, 340,
 'https://asset.msi.com/resize/image/global/product/product_1_20230103160631637e32c8dcd4d.png62405b38c58fe0f07fcef2367d8a9ba1/1024.png',
 'Raider GE78 HX',
 '{"ram_gb": 64, "storage_gb": 2048, "cpu": "Intel Core i9-14900HX", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4090", "screen_size": 17.3, "battery_hours": 6, "os": "Windows 11", "color": "Titanium Gray", "description": "MSI Raider GE78 HX - ultimate gaming laptop with RTX 4090", "good_for_gaming": true, "good_for_ml": true}'),

-- Budget
('Acer Aspire 5 15.6" Ryzen 5 8GB 256GB', 'Acer', 449.00, 'Electronics', 'laptop', 'Acer', 4.2, 3200,
 'https://static-ecemea.acer.com/media/catalog/product/a/c/acer-aspire-5-a515-56-front.png',
 'Aspire 5 A515',
 '{"ram_gb": 8, "storage_gb": 256, "cpu": "AMD Ryzen 5 7530U", "screen_size": 15.6, "battery_hours": 9, "os": "Windows 11", "color": "Steel Gray", "description": "Acer Aspire 5 - affordable everyday laptop"}');


-- =============================================================================
-- SAMPLE BOOKS
-- =============================================================================
INSERT INTO products (title, brand, price, category, product_type, source, rating, rating_count, imageurl, attributes) VALUES

('Atomic Habits by James Clear', 'Avery', 16.99, 'Books', 'book', 'Amazon', 4.8, 125000,
 'https://m.media-amazon.com/images/I/81bGKUa1e0L._SL1500_.jpg',
 '{"author": "James Clear", "genre": "Self-Help", "pages": 320, "isbn": "978-0735211292", "format": "Paperback", "description": "An easy & proven way to build good habits & break bad ones"}'),

('Dune by Frank Herbert', 'Ace', 12.99, 'Books', 'book', 'Amazon', 4.7, 89000,
 'https://m.media-amazon.com/images/I/81ym3QUd3KL._SL1500_.jpg',
 '{"author": "Frank Herbert", "genre": "Science Fiction", "pages": 688, "isbn": "978-0441013593", "format": "Paperback", "description": "The epic science fiction masterpiece"}'),

('Educated by Tara Westover', 'Random House', 14.99, 'Books', 'book', 'Amazon', 4.7, 178000,
 'https://m.media-amazon.com/images/I/81NwOj14S6L._SL1500_.jpg',
 '{"author": "Tara Westover", "genre": "Biography", "pages": 352, "isbn": "978-0399590504", "format": "Paperback", "description": "A memoir about a woman who leaves her survivalist family and goes on to earn a PhD from Cambridge University"}'),

('The Midnight Library by Matt Haig', 'Penguin', 13.99, 'Books', 'book', 'Amazon', 4.5, 92000,
 'https://m.media-amazon.com/images/I/81tCtHFtOgL._SL1500_.jpg',
 '{"author": "Matt Haig", "genre": "Fiction", "pages": 304, "isbn": "978-0525559474", "format": "Paperback", "description": "Between life and death there is a library where each book gives you the chance to try another life you could have lived"}'),

('Designing Data-Intensive Applications by Martin Kleppmann', 'O''Reilly', 39.99, 'Books', 'book', 'Amazon', 4.8, 12400,
 'https://m.media-amazon.com/images/I/91YfNb49PLL._SL1500_.jpg',
 '{"author": "Martin Kleppmann", "genre": "Technology", "pages": 616, "isbn": "978-1449373320", "format": "Paperback", "description": "The big ideas behind reliable, scalable, and maintainable systems"}');


-- =============================================================================
-- SAMPLE PHONES (Electronics)
-- =============================================================================
INSERT INTO products (title, brand, price, category, product_type, source, rating, rating_count, imageurl, attributes) VALUES

('iPhone 15 Pro Max 256GB', 'Apple', 1199.00, 'Electronics', 'phone', 'Apple', 4.7, 34000,
 'https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/iphone-15-pro-finish-select-202309-6-1inch-naturaltitanium',
 '{"storage_gb": 256, "ram_gb": 8, "screen_size": 6.7, "battery_hours": 29, "os": "iOS 17", "color": "Natural Titanium", "description": "iPhone 15 Pro Max with A17 Pro chip and titanium design"}'),

('Samsung Galaxy S24 Ultra 512GB', 'Samsung', 1419.00, 'Electronics', 'phone', 'Samsung', 4.6, 18000,
 'https://image-us.samsung.com/us/smartphones/galaxy-s24-ultra/all-galaxy-s24-ultra-702x702.jpg',
 '{"storage_gb": 512, "ram_gb": 12, "screen_size": 6.8, "battery_hours": 30, "os": "Android 14", "color": "Titanium Gray", "description": "Samsung Galaxy S24 Ultra with Galaxy AI and S Pen"}'),

('Google Pixel 8 Pro 128GB', 'Google', 999.00, 'Electronics', 'phone', 'Google', 4.5, 8900,
 'https://lh3.googleusercontent.com/2MBwGLbVDgVXm4QZNI_OHJYClQdv7cXjHsGGLfNVaR4C5V6vFkz0Fk-IMTvFhI3',
 '{"storage_gb": 128, "ram_gb": 12, "screen_size": 6.7, "battery_hours": 24, "os": "Android 14", "color": "Bay", "description": "Google Pixel 8 Pro with Tensor G3 and best-in-class camera"}');


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- Run after seeding to verify:
--   SELECT category, product_type, count(*) FROM products GROUP BY category, product_type ORDER BY 1, 2;
--   SELECT title, price, brand, category FROM products ORDER BY category, price;
