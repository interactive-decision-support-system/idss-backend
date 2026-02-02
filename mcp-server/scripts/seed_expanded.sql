-- ============================================================================
-- MCP E-commerce Database - Expanded Realistic Product Catalog
-- ============================================================================
-- 50+ realistic products across multiple categories
-- Based on real products and market prices as of 2026

-- Drop tables if they exist (for clean reseeding)
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS cart_items CASCADE;
DROP TABLE IF EXISTS carts CASCADE;
DROP TABLE IF EXISTS inventory CASCADE;
DROP TABLE IF EXISTS prices CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS product_metadata CASCADE;

-- Create products table
CREATE TABLE products (
    product_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(100),
    brand VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create product_metadata table for type-specific fields
CREATE TABLE product_metadata (
    metadata_id SERIAL PRIMARY KEY,
    product_id VARCHAR(50) NOT NULL,
    key VARCHAR(100) NOT NULL,
    value TEXT,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
);

CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_metadata_product_id ON product_metadata(product_id);

-- Create prices table
CREATE TABLE prices (
    price_id SERIAL PRIMARY KEY,
    product_id VARCHAR(50) UNIQUE NOT NULL,
    price_cents INTEGER NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
);

CREATE INDEX idx_prices_product_id ON prices(product_id);

-- Create inventory table
CREATE TABLE inventory (
    inventory_id SERIAL PRIMARY KEY,
    product_id VARCHAR(50) UNIQUE NOT NULL,
    available_qty INTEGER NOT NULL DEFAULT 0,
    reserved_qty INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
);

CREATE INDEX idx_inventory_product_id ON inventory(product_id);

-- Create carts table
CREATE TABLE carts (
    cart_id VARCHAR(50) PRIMARY KEY,
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_carts_status ON carts(status);

-- Create cart_items table
CREATE TABLE cart_items (
    cart_item_id SERIAL PRIMARY KEY,
    cart_id VARCHAR(50) NOT NULL,
    product_id VARCHAR(50) NOT NULL,
    quantity INTEGER NOT NULL,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
);

CREATE INDEX idx_cart_items_cart_id ON cart_items(cart_id);

-- Create orders table
CREATE TABLE orders (
    order_id VARCHAR(50) PRIMARY KEY,
    cart_id VARCHAR(50) NOT NULL,
    payment_method_id VARCHAR(50) NOT NULL,
    address_id VARCHAR(50) NOT NULL,
    total_cents INTEGER NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cart_id) REFERENCES carts(cart_id)
);

CREATE INDEX idx_orders_status ON orders(status);

-- ============================================================================
-- ELECTRONICS CATEGORY (20 products)
-- ============================================================================

-- Laptops
INSERT INTO products VALUES ('PROD-001', 'MacBook Pro 16" M3 Max', 'High-performance laptop with M3 Max chip, 36GB RAM, 1TB SSD, Liquid Retina XDR display', 'Electronics', 'Apple');
INSERT INTO prices VALUES (DEFAULT, 'PROD-001', 349900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-001', 12, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-001', 'processor', 'M3 Max');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-001', 'ram', '36GB');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-001', 'storage', '1TB SSD');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-001', 'screen_size', '16 inch');

INSERT INTO products VALUES ('PROD-002', 'Dell XPS 15', 'Premium Windows laptop with Intel Core i9-13900H, 32GB RAM, 1TB SSD, OLED display', 'Electronics', 'Dell');
INSERT INTO prices VALUES (DEFAULT, 'PROD-002', 229900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-002', 18, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-002', 'processor', 'Intel Core i9-13900H');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-002', 'ram', '32GB');

INSERT INTO products VALUES ('PROD-003', 'Lenovo ThinkPad X1 Carbon Gen 11', 'Ultra-portable business laptop, Intel Core i7-1370P, 16GB RAM, 512GB SSD', 'Electronics', 'Lenovo');
INSERT INTO prices VALUES (DEFAULT, 'PROD-003', 149900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-003', 25, 0);

-- Smartphones
INSERT INTO products VALUES ('PROD-004', 'iPhone 15 Pro Max', 'Latest flagship iPhone with A17 Pro chip, titanium design, 6.7" display, 256GB', 'Electronics', 'Apple');
INSERT INTO prices VALUES (DEFAULT, 'PROD-004', 119900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-004', 8, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-004', 'storage', '256GB');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-004', 'color', 'Natural Titanium');

INSERT INTO products VALUES ('PROD-005', 'Samsung Galaxy S24 Ultra', 'Flagship Android phone with Snapdragon 8 Gen 3, 12GB RAM, 512GB, S Pen', 'Electronics', 'Samsung');
INSERT INTO prices VALUES (DEFAULT, 'PROD-005', 129900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-005', 15, 0);

INSERT INTO products VALUES ('PROD-006', 'Google Pixel 8 Pro', 'AI-powered smartphone with Tensor G3 chip, excellent camera, 256GB', 'Electronics', 'Google');
INSERT INTO prices VALUES (DEFAULT, 'PROD-006', 99900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-006', 22, 0);

-- Tablets
INSERT INTO products VALUES ('PROD-007', 'iPad Pro 12.9" M2', 'Professional tablet with M2 chip, 256GB, Liquid Retina XDR display', 'Electronics', 'Apple');
INSERT INTO prices VALUES (DEFAULT, 'PROD-007', 109900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-007', 14, 0);

-- Headphones & Audio
INSERT INTO products VALUES ('PROD-008', 'Sony WH-1000XM5', 'Industry-leading noise canceling headphones with 30-hour battery life', 'Electronics', 'Sony');
INSERT INTO prices VALUES (DEFAULT, 'PROD-008', 39999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-008', 45, 0);

INSERT INTO products VALUES ('PROD-009', 'Bose QuietComfort Ultra', 'Premium noise canceling headphones with spatial audio', 'Electronics', 'Bose');
INSERT INTO prices VALUES (DEFAULT, 'PROD-009', 42900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-009', 32, 0);

INSERT INTO products VALUES ('PROD-010', 'AirPods Pro (2nd Gen)', 'Active noise cancellation, personalized spatial audio, USB-C charging', 'Electronics', 'Apple');
INSERT INTO prices VALUES (DEFAULT, 'PROD-010', 24900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-010', 67, 0);

-- Monitors
INSERT INTO products VALUES ('PROD-011', 'LG UltraGear 27" 4K', '4K gaming monitor, 144Hz, 1ms response, HDMI 2.1', 'Electronics', 'LG');
INSERT INTO prices VALUES (DEFAULT, 'PROD-011', 59999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-011', 19, 0);

INSERT INTO products VALUES ('PROD-012', 'Dell UltraSharp 32" 4K', 'Professional 4K monitor with USB-C hub, IPS Black technology', 'Electronics', 'Dell');
INSERT INTO prices VALUES (DEFAULT, 'PROD-012', 79900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-012', 11, 0);

-- Keyboards & Mice
INSERT INTO products VALUES ('PROD-013', 'Keychron Q1 Pro', 'Wireless mechanical keyboard with hot-swappable switches, aluminum frame', 'Electronics', 'Keychron');
INSERT INTO prices VALUES (DEFAULT, 'PROD-013', 21999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-013', 28, 0);

INSERT INTO products VALUES ('PROD-014', 'Logitech MX Master 3S', 'Advanced wireless mouse with 8K DPI sensor, quiet clicks', 'Electronics', 'Logitech');
INSERT INTO prices VALUES (DEFAULT, 'PROD-014', 9999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-014', 55, 0);

-- Cameras
INSERT INTO products VALUES ('PROD-015', 'Sony A7 IV', 'Full-frame mirrorless camera, 33MP, 4K 60fps video, body only', 'Electronics', 'Sony');
INSERT INTO prices VALUES (DEFAULT, 'PROD-015', 249900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-015', 7, 0);

INSERT INTO products VALUES ('PROD-016', 'Canon EOS R6 Mark II', 'Full-frame mirrorless, 24MP, excellent low-light performance', 'Electronics', 'Canon');
INSERT INTO prices VALUES (DEFAULT, 'PROD-016', 249900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-016', 9, 0);

-- Smart Home
INSERT INTO products VALUES ('PROD-017', 'Amazon Echo (4th Gen)', 'Smart speaker with Alexa, premium sound quality', 'Electronics', 'Amazon');
INSERT INTO prices VALUES (DEFAULT, 'PROD-017', 9999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-017', 120, 0);

INSERT INTO products VALUES ('PROD-018', 'Google Nest Hub (2nd Gen)', 'Smart display with Google Assistant, sleep tracking', 'Electronics', 'Google');
INSERT INTO prices VALUES (DEFAULT, 'PROD-018', 9999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-018', 85, 0);

-- Smartwatches
INSERT INTO products VALUES ('PROD-019', 'Apple Watch Series 9 GPS', 'Advanced health features, always-on Retina display, 45mm', 'Electronics', 'Apple');
INSERT INTO prices VALUES (DEFAULT, 'PROD-019', 42900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-019', 34, 0);

INSERT INTO products VALUES ('PROD-020', 'Garmin Fenix 7X Sapphire Solar', 'Premium multisport GPS watch with solar charging', 'Electronics', 'Garmin');
INSERT INTO prices VALUES (DEFAULT, 'PROD-020', 89999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-020', 6, 0);

-- ============================================================================
-- BOOKS CATEGORY (10 products)
-- ============================================================================

INSERT INTO products VALUES ('PROD-021', 'Atomic Habits', 'Bestselling book on building good habits by James Clear', 'Books', 'Penguin Random House');
INSERT INTO prices VALUES (DEFAULT, 'PROD-021', 1799, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-021', 250, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-021', 'author', 'James Clear');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-021', 'pages', '320');
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-021', 'isbn', '978-0735211292');

INSERT INTO products VALUES ('PROD-022', 'The Psychology of Money', 'Timeless lessons on wealth and happiness by Morgan Housel', 'Books', 'Harriman House');
INSERT INTO prices VALUES (DEFAULT, 'PROD-022', 1599, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-022', 180, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-022', 'author', 'Morgan Housel');

INSERT INTO products VALUES ('PROD-023', 'System Design Interview Vol 1', 'Technical interview preparation by Alex Xu', 'Books', 'ByteByteGo');
INSERT INTO prices VALUES (DEFAULT, 'PROD-023', 4999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-023', 95, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-023', 'author', 'Alex Xu');

INSERT INTO products VALUES ('PROD-024', 'Deep Work', 'Rules for focused success in a distracted world by Cal Newport', 'Books', 'Grand Central Publishing');
INSERT INTO prices VALUES (DEFAULT, 'PROD-024', 1699, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-024', 145, 0);

INSERT INTO products VALUES ('PROD-025', 'Designing Data-Intensive Applications', 'The definitive guide to scalable systems by Martin Kleppmann', 'Books', 'O\'Reilly Media');
INSERT INTO prices VALUES (DEFAULT, 'PROD-025', 5999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-025', 72, 0);

INSERT INTO products VALUES ('PROD-026', 'Thinking, Fast and Slow', 'Daniel Kahneman on decision-making and cognitive biases', 'Books', 'Farrar, Straus and Giroux');
INSERT INTO prices VALUES (DEFAULT, 'PROD-026', 1899, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-026', 220, 0);

INSERT INTO products VALUES ('PROD-027', 'The Almanack of Naval Ravikant', 'Wealth and happiness guide curated by Eric Jorgenson', 'Books', 'Magrathea Publishing');
INSERT INTO prices VALUES (DEFAULT, 'PROD-027', 2499, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-027', 130, 0);

INSERT INTO products VALUES ('PROD-028', 'Clean Code', 'A handbook of agile software craftsmanship by Robert C. Martin', 'Books', 'Prentice Hall');
INSERT INTO prices VALUES (DEFAULT, 'PROD-028', 5499, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-028', 88, 0);

INSERT INTO products VALUES ('PROD-029', 'The Lean Startup', 'How today\'s entrepreneurs use innovation by Eric Ries', 'Books', 'Crown Business');
INSERT INTO prices VALUES (DEFAULT, 'PROD-029', 1799, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-029', 165, 0);

INSERT INTO products VALUES ('PROD-030', 'Zero to One', 'Notes on startups and building the future by Peter Thiel', 'Books', 'Crown Business');
INSERT INTO prices VALUES (DEFAULT, 'PROD-030', 1899, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-030', 190, 0);

-- ============================================================================
-- HOME & KITCHEN CATEGORY (10 products)
-- ============================================================================

INSERT INTO products VALUES ('PROD-031', 'Breville Barista Express Espresso Machine', 'Built-in grinder, professional espresso at home', 'Home & Kitchen', 'Breville');
INSERT INTO prices VALUES (DEFAULT, 'PROD-031', 69999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-031', 14, 0);

INSERT INTO products VALUES ('PROD-032', 'Vitamix 5200 Blender', 'Professional-grade blender with 64oz container', 'Home & Kitchen', 'Vitamix');
INSERT INTO prices VALUES (DEFAULT, 'PROD-032', 44999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-032', 23, 0);

INSERT INTO products VALUES ('PROD-033', 'KitchenAid Artisan Stand Mixer', 'Iconic 5-quart stand mixer in Empire Red', 'Home & Kitchen', 'KitchenAid');
INSERT INTO prices VALUES (DEFAULT, 'PROD-033', 42999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-033', 31, 0);

INSERT INTO products VALUES ('PROD-034', 'Ninja Foodi 14-in-1 Pressure Cooker', 'Versatile multi-cooker with 8-quart capacity', 'Home & Kitchen', 'Ninja');
INSERT INTO prices VALUES (DEFAULT, 'PROD-034', 24999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-034', 42, 0);

INSERT INTO products VALUES ('PROD-035', 'Dyson V15 Detect Cordless Vacuum', 'Laser detection, powerful suction, up to 60min runtime', 'Home & Kitchen', 'Dyson');
INSERT INTO prices VALUES (DEFAULT, 'PROD-035', 74999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-035', 17, 0);

INSERT INTO products VALUES ('PROD-036', 'iRobot Roomba j7+', 'Self-emptying robot vacuum with obstacle avoidance', 'Home & Kitchen', 'iRobot');
INSERT INTO prices VALUES (DEFAULT, 'PROD-036', 79999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-036', 12, 0);

INSERT INTO products VALUES ('PROD-037', 'All-Clad D3 Stainless 10-Piece Cookware Set', 'Professional tri-ply stainless steel cookware', 'Home & Kitchen', 'All-Clad');
INSERT INTO prices VALUES (DEFAULT, 'PROD-037', 79900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-037', 8, 0);

INSERT INTO products VALUES ('PROD-038', 'Le Creuset Dutch Oven 5.5 Qt', 'Enameled cast iron in Cherry Red', 'Home & Kitchen', 'Le Creuset');
INSERT INTO prices VALUES (DEFAULT, 'PROD-038', 37999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-038', 19, 0);

INSERT INTO products VALUES ('PROD-039', 'Nespresso Vertuo Next', 'Coffee and espresso maker with Aeroccino milk frother', 'Home & Kitchen', 'Nespresso');
INSERT INTO prices VALUES (DEFAULT, 'PROD-039', 19999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-039', 38, 0);

INSERT INTO products VALUES ('PROD-040', 'Instant Pot Pro 10-in-1', '6-quart multi-use pressure cooker', 'Home & Kitchen', 'Instant Pot');
INSERT INTO prices VALUES (DEFAULT, 'PROD-040', 14999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-040', 56, 0);

-- ============================================================================
-- SPORTS & FITNESS CATEGORY (10 products)
-- ============================================================================

INSERT INTO products VALUES ('PROD-041', 'Nike Air Zoom Pegasus 40', 'Responsive running shoes for daily training', 'Sports', 'Nike');
INSERT INTO prices VALUES (DEFAULT, 'PROD-041', 12999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-041', 125, 0);
INSERT INTO product_metadata VALUES (DEFAULT, 'PROD-041', 'sizes', '7-13');

INSERT INTO products VALUES ('PROD-042', 'Manduka PRO Yoga Mat', 'Professional-grade 6mm mat with lifetime guarantee', 'Sports', 'Manduka');
INSERT INTO prices VALUES (DEFAULT, 'PROD-042', 12800, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-042', 67, 0);

INSERT INTO products VALUES ('PROD-043', 'Bowflex SelectTech 552 Dumbbells', 'Adjustable dumbbells 5-52.5 lbs per dumbbell', 'Sports', 'Bowflex');
INSERT INTO prices VALUES (DEFAULT, 'PROD-043', 39900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-043', 15, 0);

INSERT INTO products VALUES ('PROD-044', 'Peloton Bike+', 'Premium exercise bike with rotating touchscreen', 'Sports', 'Peloton');
INSERT INTO prices VALUES (DEFAULT, 'PROD-044', 249500, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-044', 4, 0);

INSERT INTO products VALUES ('PROD-045', 'TRX HOME2 Suspension Training', 'Complete bodyweight training system', 'Sports', 'TRX');
INSERT INTO prices VALUES (DEFAULT, 'PROD-045', 16999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-045', 43, 0);

INSERT INTO products VALUES ('PROD-046', 'Adidas Ultraboost Light', 'Lightweight running shoes with Boost cushioning', 'Sports', 'Adidas');
INSERT INTO prices VALUES (DEFAULT, 'PROD-046', 18000, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-046', 98, 0);

INSERT INTO products VALUES ('PROD-047', 'Yeti Rambler 26oz Water Bottle', 'Insulated stainless steel bottle', 'Sports', 'Yeti');
INSERT INTO prices VALUES (DEFAULT, 'PROD-047', 3800, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-047', 210, 0);

INSERT INTO products VALUES ('PROD-048', 'Fitbit Charge 6', 'Advanced fitness tracker with built-in GPS', 'Sports', 'Fitbit');
INSERT INTO prices VALUES (DEFAULT, 'PROD-048', 15999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-048', 78, 0);

INSERT INTO products VALUES ('PROD-049', 'Theragun Prime Massage Gun', 'Percussive therapy device for muscle recovery', 'Sports', 'Therabody');
INSERT INTO prices VALUES (DEFAULT, 'PROD-049', 29999, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-049', 29, 0);

INSERT INTO products VALUES ('PROD-050', 'REI Co-op Flash 55 Backpack', 'Lightweight backpacking pack for multi-day trips', 'Sports', 'REI');
INSERT INTO prices VALUES (DEFAULT, 'PROD-050', 19900, 'USD');
INSERT INTO inventory VALUES (DEFAULT, 'PROD-050', 24, 0);

-- Create sample cart
INSERT INTO carts VALUES ('cart-test-001', 'active');

-- Summary
SELECT 'Total Products: ' || COUNT(*) FROM products;
SELECT 'Total Prices: ' || COUNT(*) FROM prices;
SELECT 'Total Inventory: ' || COUNT(*) FROM inventory;
SELECT 'Total Metadata: ' || COUNT(*) FROM product_metadata;

-- Sample query
SELECT 
    p.product_id,
    p.name,
    p.category,
    p.brand,
    pr.price_cents / 100.0 AS price_dollars,
    i.available_qty
FROM products p
JOIN prices pr ON p.product_id = pr.product_id
JOIN inventory i ON p.product_id = i.product_id
ORDER BY p.category, p.product_id
LIMIT 10;
