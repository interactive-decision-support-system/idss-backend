-- MCP E-commerce Database Seed Data
-- Creates tables and populates with 10 sample products

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

-- Insert 10 sample products
-- Product 1: High-end laptop
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-laptop-001', 'ThinkPad X1 Carbon Gen 11', 'Ultra-portable business laptop with 14-inch display, Intel Core i7, 16GB RAM, 512GB SSD. Perfect for professionals who need power and portability.', 'Electronics', 'Lenovo');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-001', 149999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-001', 25);

-- Product 2: Wireless headphones
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-headphones-001', 'Sony WH-1000XM5', 'Industry-leading noise canceling wireless headphones with premium sound quality and 30-hour battery life.', 'Electronics', 'Sony');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-headphones-001', 39999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-headphones-001', 50);

-- Product 3: Coffee maker
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-coffee-001', 'Breville Barista Express', 'Espresso machine with built-in grinder, perfect for home baristas who want cafe-quality espresso.', 'Home & Kitchen', 'Breville');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-coffee-001', 69999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-coffee-001', 15);

-- Product 4: Running shoes
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-shoes-001', 'Nike Air Zoom Pegasus 40', 'Responsive running shoes with excellent cushioning for daily training and long runs.', 'Sports', 'Nike');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-shoes-001', 12999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-shoes-001', 100);

-- Product 5: Standing desk
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-desk-001', 'Uplift V2 Standing Desk', 'Electric height-adjustable standing desk with programmable memory settings. 60x30 inch desktop.', 'Furniture', 'Uplift');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-desk-001', 79900);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-desk-001', 8);

-- Product 6: Smartphone (low stock for testing OUT_OF_STOCK)
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-phone-001', 'iPhone 15 Pro', 'Latest flagship iPhone with A17 Pro chip, titanium design, and advanced camera system.', 'Electronics', 'Apple');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-phone-001', 99999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-phone-001', 2);

-- Product 7: Book
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-book-001', 'Atomic Habits', 'Bestselling book on building good habits and breaking bad ones by James Clear.', 'Books', 'Penguin Random House');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-001', 1799);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-001', 200);

-- Product 8: Mechanical keyboard
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-keyboard-001', 'Keychron Q1 Pro', 'Premium wireless mechanical keyboard with hot-swappable switches and aluminum frame.', 'Electronics', 'Keychron');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-keyboard-001', 21999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-keyboard-001', 30);

-- Product 9: Yoga mat
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-yoga-001', 'Manduka PRO Yoga Mat', 'Professional-grade yoga mat with superior cushioning and lifetime guarantee.', 'Sports', 'Manduka');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-yoga-001', 12800);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-yoga-001', 45);

-- Product 10: Monitor
INSERT INTO products (product_id, name, description, category, brand) VALUES
('prod-monitor-001', 'LG UltraGear 27-inch 4K Gaming Monitor', '4K UHD gaming monitor with 144Hz refresh rate, 1ms response time, and HDR support.', 'Electronics', 'LG');

INSERT INTO prices (product_id, price_cents) VALUES ('prod-monitor-001', 59999);
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-monitor-001', 20);

-- Create one sample cart for testing
INSERT INTO carts (cart_id, status) VALUES ('cart-test-001', 'active');

-- Verify data
SELECT 'Products created: ' || COUNT(*) FROM products;
SELECT 'Prices created: ' || COUNT(*) FROM prices;
SELECT 'Inventory records created: ' || COUNT(*) FROM inventory;

-- Show sample products with prices
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
ORDER BY p.product_id;
