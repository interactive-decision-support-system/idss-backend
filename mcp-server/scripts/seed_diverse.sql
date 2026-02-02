-- MCP E-commerce Database - Diverse Product Catalog
-- Creates tables and populates with 50+ diverse products across all categories

-- Drop tables if they exist (for clean reseeding)
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS cart_items CASCADE;
DROP TABLE IF EXISTS carts CASCADE;
DROP TABLE IF EXISTS inventory CASCADE;
DROP TABLE IF EXISTS prices CASCADE;
DROP TABLE IF EXISTS products CASCADE;

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

CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_brand ON products(brand);

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
-- LAPTOPS (Electronics) - 15 diverse laptops
-- ============================================================================

-- Budget Laptops
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-001', 'Acer Aspire 5', 'Budget-friendly 15.6-inch laptop with AMD Ryzen 5, 8GB RAM, 256GB SSD. Great for students and everyday use.', 'Electronics', 'Acer');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-001', 44999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-001', 40);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-002', 'HP Pavilion 15', '15.6-inch laptop with Intel Core i5, 8GB RAM, 512GB SSD. Reliable performance for work and entertainment.', 'Electronics', 'HP');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-002', 59999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-002', 35);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-003', 'Lenovo IdeaPad 3', '14-inch lightweight laptop with AMD Ryzen 3, 8GB RAM, 256GB SSD. Perfect for students.', 'Electronics', 'Lenovo');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-003', 39999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-003', 50);

-- Mid-Range Laptops
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-004', 'Dell XPS 15 OLED', 'Premium 15.6-inch OLED laptop with Intel Core i7, 16GB RAM, 512GB SSD. Stunning display for creative work.', 'Electronics', 'Dell');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-004', 229999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-004', 18);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-005', 'MacBook Pro 16" M3 Max', 'Powerful 16-inch MacBook with M3 Max chip, 32GB RAM, 1TB SSD. Ultimate performance for professionals.', 'Electronics', 'Apple');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-005', 349999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-005', 12);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-006', 'ASUS ROG Zephyrus G14', 'Gaming laptop with AMD Ryzen 9, NVIDIA RTX 4060, 16GB RAM, 1TB SSD. Compact powerhouse for gamers.', 'Electronics', 'ASUS');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-006', 159999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-006', 10);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-007', 'HP Spectre x360 14"', 'Convertible 2-in-1 laptop with Intel Core i7, 16GB RAM, 512GB SSD. Versatile design for work and play.', 'Electronics', 'HP');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-007', 129999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-007', 15);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-008', 'ThinkPad X1 Carbon Gen 11', 'Ultra-portable business laptop with 14-inch display, Intel Core i7, 16GB RAM, 512GB SSD.', 'Electronics', 'Lenovo');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-008', 149999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-008', 25);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-009', 'MacBook Air M2 13"', 'Lightweight 13-inch MacBook with M2 chip, 8GB RAM, 256GB SSD. Perfect balance of power and portability.', 'Electronics', 'Apple');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-009', 109999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-009', 30);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-010', 'Microsoft Surface Laptop 5', 'Premium 13.5-inch laptop with Intel Core i5, 8GB RAM, 256GB SSD. Sleek design with excellent build quality.', 'Electronics', 'Microsoft');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-010', 99999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-010', 20);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-011', 'Razer Blade 15', 'Gaming laptop with Intel Core i7, NVIDIA RTX 4070, 16GB RAM, 1TB SSD. Premium gaming performance.', 'Electronics', 'Razer');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-011', 249999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-011', 8);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-012', 'LG Gram 17', 'Ultra-lightweight 17-inch laptop with Intel Core i7, 16GB RAM, 1TB SSD. Massive screen in a portable package.', 'Electronics', 'LG');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-012', 179999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-012', 12);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-013', 'Samsung Galaxy Book3 Pro', '15.6-inch AMOLED laptop with Intel Core i7, 16GB RAM, 512GB SSD. Vibrant display for content creation.', 'Electronics', 'Samsung');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-013', 139999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-013', 15);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-014', 'Framework Laptop 13', 'Modular 13-inch laptop with Intel Core i5, 8GB RAM, 256GB SSD. Repairable and upgradeable design.', 'Electronics', 'Framework');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-014', 104999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-014', 22);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-015', 'Alienware m18', 'Massive 18-inch gaming laptop with Intel Core i9, NVIDIA RTX 4090, 32GB RAM, 2TB SSD. Ultimate gaming machine.', 'Electronics', 'Alienware');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-015', 399999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-015', 5);

-- ============================================================================
-- HEADPHONES (Electronics) - 10 diverse headphones
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-001', 'Sony WH-1000XM5', 'Industry-leading noise canceling wireless headphones with premium sound quality and 30-hour battery life.', 'Electronics', 'Sony');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-001', 39999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-001', 50);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-002', 'Bose QuietComfort 45', 'Premium noise-canceling headphones with exceptional comfort and sound quality. 24-hour battery life.', 'Electronics', 'Bose');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-002', 32999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-002', 40);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-003', 'Apple AirPods Pro 2', 'Wireless earbuds with active noise cancellation, spatial audio, and MagSafe charging case.', 'Electronics', 'Apple');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-003', 24999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-003', 75);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-004', 'Sennheiser Momentum 4', 'Premium wireless headphones with 60-hour battery life and exceptional sound quality.', 'Electronics', 'Sennheiser');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-004', 34999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-004', 30);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-005', 'JBL Tune 770NC', 'Affordable noise-canceling headphones with 35-hour battery life and JBL signature sound.', 'Electronics', 'JBL');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-005', 12999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-005', 60);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-006', 'Sony WF-1000XM5', 'Premium true wireless earbuds with industry-leading noise cancellation and 8-hour battery.', 'Electronics', 'Sony');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-006', 29999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-006', 45);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-007', 'Beats Studio Pro', 'Wireless over-ear headphones with active noise cancellation and spatial audio support.', 'Electronics', 'Beats');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-007', 34999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-007', 35);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-008', 'Audio-Technica ATH-M50xBT2', 'Professional wireless headphones with studio-quality sound and 50-hour battery life.', 'Electronics', 'Audio-Technica');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-008', 19999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-008', 40);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-009', 'Samsung Galaxy Buds2 Pro', 'Premium wireless earbuds with active noise cancellation and 360 Audio support.', 'Electronics', 'Samsung');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-009', 19999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-009', 55);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-010', 'Anker Soundcore Q30', 'Budget-friendly noise-canceling headphones with 40-hour battery life and excellent value.', 'Electronics', 'Anker');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-010', 7999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-010', 80);

-- ============================================================================
-- BOOKS - 10 diverse books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-001', 'Atomic Habits', 'Bestselling book on building good habits and breaking bad ones by James Clear.', 'Books', 'Penguin Random House');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-001', 1799);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-001', 200);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-002', 'The Psychology of Money', 'Timeless lessons on wealth, greed, and happiness by Morgan Housel.', 'Books', 'Harriman House');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-002', 1599);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-002', 180);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-003', 'System Design Interview Vol 1', 'Comprehensive guide to system design interviews by Alex Xu.', 'Books', 'ByteByteGo');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-003', 4999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-003', 95);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-004', 'The Seven Husbands of Evelyn Hugo', 'Bestselling novel by Taylor Jenkins Reid about a reclusive Hollywood icon.', 'Books', 'Atria Books');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-004', 1699);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-004', 150);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-005', 'Clean Code', 'A Handbook of Agile Software Craftsmanship by Robert C. Martin.', 'Books', 'Prentice Hall');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-005', 5499);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-005', 120);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-006', 'Project Hail Mary', 'Science fiction novel by Andy Weir about an astronaut on a desperate mission.', 'Books', 'Ballantine Books');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-006', 1899);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-006', 140);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-007', 'The Midnight Library', 'Novel by Matt Haig about a library between life and death with infinite books.', 'Books', 'Viking');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-007', 1699);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-007', 160);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-008', 'Designing Data-Intensive Applications', 'The Big Ideas Behind Reliable, Scalable, and Maintainable Systems by Martin Kleppmann.', 'Books', 'O''Reilly Media');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-008', 5999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-008', 90);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-009', 'Dune', 'Epic science fiction novel by Frank Herbert, the first book in the Dune Chronicles.', 'Books', 'Ace Books');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-009', 999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-009', 250);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-010', 'Educated', 'Memoir by Tara Westover about growing up in a survivalist family and pursuing education.', 'Books', 'Random House');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-010', 1799);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-010', 130);

-- ============================================================================
-- OTHER ELECTRONICS - 10 diverse products
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-monitor-001', 'LG UltraGear 27-inch 4K Gaming Monitor', '4K UHD gaming monitor with 144Hz refresh rate, 1ms response time, and HDR support.', 'Electronics', 'LG');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-monitor-001', 59999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-monitor-001', 20);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-keyboard-001', 'Keychron Q1 Pro', 'Premium wireless mechanical keyboard with hot-swappable switches and aluminum frame.', 'Electronics', 'Keychron');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-keyboard-001', 21999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-keyboard-001', 30);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-phone-001', 'iPhone 15 Pro', 'Latest flagship iPhone with A17 Pro chip, titanium design, and advanced camera system.', 'Electronics', 'Apple');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-phone-001', 99999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-phone-001', 2);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-tablet-001', 'iPad Pro 12.9" M2', '12.9-inch tablet with M2 chip, 256GB storage, and Liquid Retina XDR display.', 'Electronics', 'Apple');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-tablet-001', 109999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-tablet-001', 15);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-smartwatch-001', 'Apple Watch Series 9', 'Latest Apple Watch with S9 chip, always-on display, and advanced health features.', 'Electronics', 'Apple');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-smartwatch-001', 39999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-smartwatch-001', 40);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-speaker-001', 'Sonos Era 300', 'Premium smart speaker with spatial audio and voice control.', 'Electronics', 'Sonos');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-speaker-001', 44999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-speaker-001', 25);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-camera-001', 'Sony Alpha 7 IV', 'Full-frame mirrorless camera with 33MP sensor and 4K video recording.', 'Electronics', 'Sony');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-camera-001', 249999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-camera-001', 8);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-mouse-001', 'Logitech MX Master 3S', 'Premium wireless mouse with advanced ergonomics and precision tracking.', 'Electronics', 'Logitech');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-mouse-001', 9999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-mouse-001', 50);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-webcam-001', 'Logitech Brio 4K', 'Ultra HD webcam with 4K video, HDR, and advanced autofocus.', 'Electronics', 'Logitech');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-webcam-001', 19999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-webcam-001', 35);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-router-001', 'ASUS ROG Rapture GT-AX11000', 'Tri-band WiFi 6 gaming router with 10Gbps ports and gaming optimization.', 'Electronics', 'ASUS');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-router-001', 59999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-router-001', 12);

-- ============================================================================
-- CLOTHING - 5 diverse items
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-jeans-001', 'Levi''s 501 Original Fit Jeans', 'Classic straight-leg jeans in original fit. Timeless style and durable construction.', 'Clothing', 'Levi''s');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-jeans-001', 6999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-jeans-001', 40);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-shoes-001', 'Nike Air Max 270 Men''s Running Shoes', 'Responsive running shoes with excellent cushioning for daily training and long runs.', 'Clothing', 'Nike');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-shoes-001', 15999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-shoes-001', 25);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-jacket-001', 'Patagonia Nano Puff Jacket', 'Lightweight insulated jacket with recycled polyester insulation. Perfect for outdoor adventures.', 'Clothing', 'Patagonia');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-jacket-001', 19999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-jacket-001', 30);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-tshirt-001', 'Uniqlo Supima Cotton T-Shirt', 'Premium cotton t-shirt made from Supima cotton. Soft, durable, and comfortable.', 'Clothing', 'Uniqlo');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-tshirt-001', 1499);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-tshirt-001', 100);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-hoodie-001', 'Champion Reverse Weave Hoodie', 'Classic heavyweight hoodie with reverse weave construction for durability.', 'Clothing', 'Champion');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-hoodie-001', 5999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-hoodie-001', 45);

-- ============================================================================
-- SPORTS - 5 diverse items
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-yoga-001', 'Manduka PRO Yoga Mat', 'Professional-grade yoga mat with superior cushioning and lifetime guarantee.', 'Sports', 'Manduka');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-yoga-001', 12800);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-yoga-001', 45);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-dumbbell-001', 'Bowflex SelectTech 552 Adjustable Dumbbells', 'Space-saving adjustable dumbbells that replace 15 sets of traditional weights.', 'Sports', 'Bowflex');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-dumbbell-001', 39999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-dumbbell-001', 10);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-bike-001', 'Peloton Bike', 'Indoor cycling bike with 22-inch touchscreen and live/on-demand classes.', 'Sports', 'Peloton');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-bike-001', 144500);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-bike-001', 5);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-basketball-001', 'Spalding NBA Official Game Basketball', 'Official NBA game basketball with composite leather cover.', 'Sports', 'Spalding');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-basketball-001', 5999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-basketball-001', 60);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-treadmill-001', 'NordicTrack Commercial 1750', 'Premium treadmill with 10-inch touchscreen and iFit compatibility.', 'Sports', 'NordicTrack');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-treadmill-001', 199900);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-treadmill-001', 3);

-- ============================================================================
-- HOME & KITCHEN - 5 diverse items
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-coffee-001', 'Breville Barista Express', 'Espresso machine with built-in grinder, perfect for home baristas who want cafe-quality espresso.', 'Home & Kitchen', 'Breville');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-coffee-001', 69999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-coffee-001', 15);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-vacuum-001', 'Dyson V15 Detect', 'Cordless vacuum with laser technology to reveal microscopic dust and powerful suction.', 'Home & Kitchen', 'Dyson');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-vacuum-001', 74999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-vacuum-001', 12);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-blender-001', 'Vitamix 5200', 'Professional-grade blender with powerful motor for smoothies, soups, and more.', 'Home & Kitchen', 'Vitamix');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-blender-001', 44999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-blender-001', 20);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-airfryer-001', 'Ninja Foodi Dual Zone', 'Dual-zone air fryer with two independent cooking zones for versatile meal preparation.', 'Home & Kitchen', 'Ninja');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-airfryer-001', 24999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-airfryer-001', 30);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-standmixer-001', 'KitchenAid Artisan Stand Mixer', '5-quart stand mixer with 10-speed control and multiple attachments.', 'Home & Kitchen', 'KitchenAid');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-standmixer-001', 37999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-standmixer-001', 18);

-- ============================================================================
-- FURNITURE - 3 items
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-desk-001', 'Uplift V2 Standing Desk', 'Electric height-adjustable standing desk with programmable memory settings. 60x30 inch desktop.', 'Furniture', 'Uplift');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-desk-001', 79900);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-desk-001', 8);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-chair-001', 'Herman Miller Aeron Chair', 'Ergonomic office chair with PostureFit SL support and adjustable armrests.', 'Furniture', 'Herman Miller');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-chair-001', 139500);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-chair-001', 6);

INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-lamp-001', 'Philips Hue Smart Light Strip', 'Color-changing LED light strip with smart home integration and app control.', 'Furniture', 'Philips');
INSERT INTO prices (product_id, price_cents) VALUES ('prod-lamp-001', 8999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-lamp-001', 40);

-- Create one sample cart for testing
INSERT INTO carts (cart_id, status) VALUES ('cart-test-001', 'active');

-- Verify data
SELECT 'Products created: ' || COUNT(*) FROM products;
SELECT 'Prices created: ' || COUNT(*) FROM prices;
SELECT 'Inventory records created: ' || COUNT(*) FROM inventory;

-- Show summary by category
SELECT 
    category,
    COUNT(*) as product_count,
    MIN(pr.price_cents / 100.0) as min_price,
    MAX(pr.price_cents / 100.0) as max_price
FROM products p
JOIN prices pr ON p.product_id = pr.product_id
GROUP BY category
ORDER BY category;
