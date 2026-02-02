-- ============================================================================
-- EXPANDED LAPTOP DATABASE WITH SUBCATEGORIES
-- ============================================================================
-- Adds 30+ more laptops with proper subcategory assignments
-- Subcategories: School, Gaming, Work, Creative work

-- ============================================================================
-- SCHOOL LAPTOPS (10 laptops)
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-001', 'Acer Chromebook 314', '14-inch Chromebook with Intel Celeron, 4GB RAM, 64GB eMMC. Perfect for students with long battery life.', 'Electronics', 'Acer', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-001', 24999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 24999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-001', 60) ON CONFLICT (product_id) DO UPDATE SET available_qty = 60;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-002', 'HP Chromebook 14', '14-inch Chromebook with MediaTek processor, 4GB RAM, 64GB storage. Lightweight and affordable for students.', 'Electronics', 'HP', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-002', 19999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 19999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-002', 75) ON CONFLICT (product_id) DO UPDATE SET available_qty = 75;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-003', 'Lenovo Chromebook Duet 3', '10.9-inch 2-in-1 Chromebook with detachable keyboard. Perfect for note-taking and studying.', 'Electronics', 'Lenovo', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-003', 34999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 34999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-003', 45) ON CONFLICT (product_id) DO UPDATE SET available_qty = 45;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-004', 'Dell Inspiron 15 3000', '15.6-inch laptop with Intel Core i3, 8GB RAM, 256GB SSD. Reliable performance for schoolwork and research.', 'Electronics', 'Dell', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-004', 44999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 44999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-004', 55) ON CONFLICT (product_id) DO UPDATE SET available_qty = 55;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-005', 'ASUS VivoBook 15', '15.6-inch laptop with AMD Ryzen 5, 8GB RAM, 256GB SSD. Great value for students who need performance.', 'Electronics', 'ASUS', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-005', 49999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 49999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-005', 50) ON CONFLICT (product_id) DO UPDATE SET available_qty = 50;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-006', 'Microsoft Surface Go 3', '10.5-inch 2-in-1 tablet with detachable keyboard. Portable and perfect for taking notes in class.', 'Electronics', 'Microsoft', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-006', 54999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 54999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-006', 40) ON CONFLICT (product_id) DO UPDATE SET available_qty = 40;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-007', 'Acer Swift 3', '14-inch ultraportable laptop with AMD Ryzen 5, 8GB RAM, 512GB SSD. Lightweight design perfect for carrying to class.', 'Electronics', 'Acer', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-007', 59999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 59999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-007', 48) ON CONFLICT (product_id) DO UPDATE SET available_qty = 48;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-008', 'HP 15.6" Laptop', '15.6-inch laptop with Intel Core i5, 8GB RAM, 256GB SSD. Durable build for daily school use.', 'Electronics', 'HP', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-008', 54999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 54999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-008', 52) ON CONFLICT (product_id) DO UPDATE SET available_qty = 52;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-009', 'Lenovo IdeaPad Flex 5', '14-inch 2-in-1 convertible laptop with AMD Ryzen 5, 8GB RAM, 256GB SSD. Versatile for note-taking and presentations.', 'Electronics', 'Lenovo', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-009', 64999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 64999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-009', 42) ON CONFLICT (product_id) DO UPDATE SET available_qty = 42;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-010', 'Dell Latitude 3420', '14-inch business-class laptop with Intel Core i5, 8GB RAM, 256GB SSD. Built to last through college years.', 'Electronics', 'Dell', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-010', 69999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 69999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-010', 38) ON CONFLICT (product_id) DO UPDATE SET available_qty = 38;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-011', 'Samsung Galaxy Chromebook 4', '11.6-inch Chromebook with Intel Celeron, 4GB RAM, 32GB eMMC. Compact and affordable for K-12 students.', 'Electronics', 'Samsung', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-011', 17999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 17999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-011', 80) ON CONFLICT (product_id) DO UPDATE SET available_qty = 80;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-012', 'Google Pixelbook Go', '13.3-inch Chromebook with Intel Core m3, 8GB RAM, 64GB storage. Premium build for college students.', 'Electronics', 'Google', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-012', 64999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 64999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-012', 35) ON CONFLICT (product_id) DO UPDATE SET available_qty = 35;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-013', 'Lenovo ThinkPad E15', '15.6-inch laptop with Intel Core i5, 8GB RAM, 256GB SSD. Durable keyboard for long writing sessions.', 'Electronics', 'Lenovo', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-013', 59999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 59999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-013', 45) ON CONFLICT (product_id) DO UPDATE SET available_qty = 45;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-014', 'ASUS Chromebook Flip C434', '14-inch 2-in-1 Chromebook with Intel Core m3, 4GB RAM, 64GB storage. Convertible design for presentations.', 'Electronics', 'ASUS', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-014', 49999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 49999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-014', 50) ON CONFLICT (product_id) DO UPDATE SET available_qty = 50;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-school-015', 'HP Pavilion 15.6" Laptop', '15.6-inch laptop with AMD Ryzen 3, 8GB RAM, 256GB SSD. Budget-friendly option for students.', 'Electronics', 'HP', 'School')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'School';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-school-015', 39999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 39999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-school-015', 65) ON CONFLICT (product_id) DO UPDATE SET available_qty = 65;

-- ============================================================================
-- GAMING LAPTOPS (8 laptops)
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-001', 'MSI Katana GF66', '15.6-inch gaming laptop with Intel Core i7, NVIDIA RTX 4050, 16GB RAM, 512GB SSD. Entry-level gaming performance.', 'Electronics', 'MSI', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-001', 89999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 89999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-001', 25) ON CONFLICT (product_id) DO UPDATE SET available_qty = 25;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-002', 'ASUS TUF Gaming A15', '15.6-inch gaming laptop with AMD Ryzen 7, NVIDIA RTX 4060, 16GB RAM, 512GB SSD. Durable gaming machine.', 'Electronics', 'ASUS', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-002', 119999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 119999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-002', 20) ON CONFLICT (product_id) DO UPDATE SET available_qty = 20;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-003', 'HP Victus 16', '16.1-inch gaming laptop with AMD Ryzen 5, NVIDIA RTX 3050, 8GB RAM, 512GB SSD. Great for casual gaming.', 'Electronics', 'HP', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-003', 79999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 79999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-003', 30) ON CONFLICT (product_id) DO UPDATE SET available_qty = 30;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-004', 'Lenovo Legion 5 Pro', '16-inch gaming laptop with AMD Ryzen 7, NVIDIA RTX 4070, 16GB RAM, 1TB SSD. High-refresh display for competitive gaming.', 'Electronics', 'Lenovo', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-004', 149999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 149999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-004', 15) ON CONFLICT (product_id) DO UPDATE SET available_qty = 15;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-005', 'Acer Predator Helios 300', '15.6-inch gaming laptop with Intel Core i7, NVIDIA RTX 4060, 16GB RAM, 1TB SSD. Excellent cooling for long gaming sessions.', 'Electronics', 'Acer', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-005', 129999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 129999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-005', 18) ON CONFLICT (product_id) DO UPDATE SET available_qty = 18;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-006', 'Dell G15 5520', '15.6-inch gaming laptop with Intel Core i7, NVIDIA RTX 3060, 16GB RAM, 512GB SSD. Solid gaming performance at mid-range price.', 'Electronics', 'Dell', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-006', 109999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 109999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-006', 22) ON CONFLICT (product_id) DO UPDATE SET available_qty = 22;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-007', 'ASUS ROG Strix G16', '16-inch gaming laptop with Intel Core i9, NVIDIA RTX 4080, 32GB RAM, 1TB SSD. Top-tier gaming performance.', 'Electronics', 'ASUS', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-007', 249999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 249999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-007', 12) ON CONFLICT (product_id) DO UPDATE SET available_qty = 12;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-gaming-008', 'Razer Blade 14', '14-inch compact gaming laptop with AMD Ryzen 9, NVIDIA RTX 4070, 16GB RAM, 1TB SSD. Premium build in portable form.', 'Electronics', 'Razer', 'Gaming')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Gaming';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-gaming-008', 219999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 219999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-gaming-008', 10) ON CONFLICT (product_id) DO UPDATE SET available_qty = 10;

-- ============================================================================
-- WORK LAPTOPS (8 laptops)
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-001', 'Dell Latitude 5430', '14-inch business laptop with Intel Core i5, 16GB RAM, 512GB SSD. Enterprise-grade security and durability.', 'Electronics', 'Dell', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-001', 99999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 99999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-001', 35) ON CONFLICT (product_id) DO UPDATE SET available_qty = 35;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-002', 'HP EliteBook 840', '14-inch business laptop with Intel Core i7, 16GB RAM, 512GB SSD. Premium build for professionals.', 'Electronics', 'HP', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-002', 119999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 119999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-002', 28) ON CONFLICT (product_id) DO UPDATE SET available_qty = 28;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-003', 'Lenovo ThinkPad E14', '14-inch business laptop with AMD Ryzen 5, 16GB RAM, 512GB SSD. Classic ThinkPad reliability.', 'Electronics', 'Lenovo', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-003', 79999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 79999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-003', 40) ON CONFLICT (product_id) DO UPDATE SET available_qty = 40;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-004', 'Microsoft Surface Laptop Studio', '14.4-inch 2-in-1 business laptop with Intel Core i7, 16GB RAM, 512GB SSD. Innovative design for productivity.', 'Electronics', 'Microsoft', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-004', 179999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 179999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-004', 18) ON CONFLICT (product_id) DO UPDATE SET available_qty = 18;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-005', 'ASUS ExpertBook B9', '14-inch ultraportable business laptop with Intel Core i7, 16GB RAM, 1TB SSD. Lightweight yet powerful.', 'Electronics', 'ASUS', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-005', 139999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 139999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-005', 22) ON CONFLICT (product_id) DO UPDATE SET available_qty = 22;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-006', 'Acer TravelMate P4', '14-inch business laptop with Intel Core i5, 8GB RAM, 256GB SSD. Durable design for business travel.', 'Electronics', 'Acer', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-006', 69999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 69999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-006', 32) ON CONFLICT (product_id) DO UPDATE SET available_qty = 32;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-007', 'HP ProBook 450', '15.6-inch business laptop with Intel Core i5, 16GB RAM, 512GB SSD. Large screen for multitasking.', 'Electronics', 'HP', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-007', 94999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 94999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-007', 30) ON CONFLICT (product_id) DO UPDATE SET available_qty = 30;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-work-008', 'Dell Precision 5570', '15.6-inch mobile workstation with Intel Core i7, 32GB RAM, 1TB SSD. Professional-grade performance.', 'Electronics', 'Dell', 'Work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-work-008', 199999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 199999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-work-008', 15) ON CONFLICT (product_id) DO UPDATE SET available_qty = 15;

-- ============================================================================
-- CREATIVE WORK LAPTOPS (6 laptops)
-- ============================================================================

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-creative-001', 'MacBook Pro 14" M3', '14-inch MacBook with M3 chip, 18GB RAM, 512GB SSD. Perfect for video editing and design work.', 'Electronics', 'Apple', 'Creative work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Creative work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-creative-001', 199999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 199999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-creative-001', 20) ON CONFLICT (product_id) DO UPDATE SET available_qty = 20;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-creative-002', 'Dell XPS 17', '17-inch large-screen laptop with Intel Core i7, 32GB RAM, 1TB SSD. Massive display for creative professionals.', 'Electronics', 'Dell', 'Creative work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Creative work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-creative-002', 249999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 249999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-creative-002', 12) ON CONFLICT (product_id) DO UPDATE SET available_qty = 12;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-creative-003', 'ASUS ProArt Studiobook 16', '16-inch creator laptop with AMD Ryzen 9, NVIDIA RTX 4060, 32GB RAM, 1TB SSD. Designed for content creators.', 'Electronics', 'ASUS', 'Creative work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Creative work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-creative-003', 229999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 229999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-creative-003', 10) ON CONFLICT (product_id) DO UPDATE SET available_qty = 10;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-creative-004', 'HP ZBook Studio G9', '16-inch mobile workstation with Intel Core i7, 32GB RAM, 1TB SSD. Professional graphics for 3D work.', 'Electronics', 'HP', 'Creative work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Creative work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-creative-004', 279999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 279999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-creative-004', 8) ON CONFLICT (product_id) DO UPDATE SET available_qty = 8;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-creative-005', 'Microsoft Surface Laptop Studio 2', '14.4-inch 2-in-1 with Intel Core i7, 32GB RAM, 1TB SSD. Versatile for drawing and design.', 'Electronics', 'Microsoft', 'Creative work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Creative work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-creative-005', 249999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 249999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-creative-005', 14) ON CONFLICT (product_id) DO UPDATE SET available_qty = 14;

INSERT INTO products (product_id, name, description, category, brand, subcategory) VALUES
('prod-laptop-creative-006', 'MacBook Air 15" M3', '15-inch MacBook with M3 chip, 16GB RAM, 512GB SSD. Large screen for creative work on the go.', 'Electronics', 'Apple', 'Creative work')
ON CONFLICT (product_id) DO UPDATE SET subcategory = 'Creative work';
INSERT INTO prices (product_id, price_cents) VALUES ('prod-laptop-creative-006', 169999) ON CONFLICT (product_id) DO UPDATE SET price_cents = 169999;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-laptop-creative-006', 25) ON CONFLICT (product_id) DO UPDATE SET available_qty = 25;
