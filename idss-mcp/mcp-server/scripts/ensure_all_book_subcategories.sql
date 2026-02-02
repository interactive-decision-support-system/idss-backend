-- Ensure all book subcategories have products
-- This script adds books to any subcategories that are missing or have few books

-- Check current counts
SELECT 'Current book counts by subcategory:' as info;
SELECT subcategory, COUNT(*) as count 
FROM products 
WHERE category = 'Books' 
GROUP BY subcategory 
ORDER BY subcategory;

-- ============================================================================
-- Add more Sci-Fi books (ensure we have enough)
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-extra-001', 'The Expanse: Leviathan Wakes', 'First book in the acclaimed Expanse series by James S.A. Corey. A space opera thriller.', 'Books', 'Sci-Fi', 'Orbit')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-extra-001', 1699)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1699;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-extra-001', 120)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 120;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-extra-002', 'Red Mars', 'First book in Kim Stanley Robinson''s Mars trilogy. Hard science fiction about terraforming Mars.', 'Books', 'Sci-Fi', 'Bantam Spectra')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-extra-002', 1899)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1899;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-extra-002', 110)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 110;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-extra-003', 'The Stars My Destination', 'Classic science fiction novel by Alfred Bester about revenge and teleportation.', 'Books', 'Sci-Fi', 'Vintage')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-extra-003', 1499)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1499;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-extra-003', 130)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 130;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-extra-004', 'Children of Time', 'Award-winning science fiction novel by Adrian Tchaikovsky about evolution and alien intelligence.', 'Books', 'Sci-Fi', 'Tor Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-extra-004', 1799)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1799;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-extra-004', 115)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 115;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-extra-005', 'The Long Earth', 'Science fiction novel by Terry Pratchett and Stephen Baxter about parallel Earths.', 'Books', 'Sci-Fi', 'Harper')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-extra-005', 1699)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1699;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-extra-005', 125)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 125;

-- ============================================================================
-- Add more Fiction books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-extra-001', 'The Lord of the Rings', 'Epic fantasy trilogy by J.R.R. Tolkien. The complete story of Frodo and the One Ring.', 'Books', 'Fiction', 'Houghton Mifflin')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-extra-001', 2999)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 2999;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-extra-001', 100)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 100;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-extra-002', 'One Hundred Years of Solitude', 'Magical realism masterpiece by Gabriel García Márquez about the Buendía family.', 'Books', 'Fiction', 'Harper Perennial')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-extra-002', 1899)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1899;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-extra-002', 140)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 140;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-extra-003', 'Beloved', 'Pulitzer Prize-winning novel by Toni Morrison about slavery and its aftermath.', 'Books', 'Fiction', 'Vintage')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-extra-003', 1799)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1799;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-extra-003', 135)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 135;

-- ============================================================================
-- Add more Mystery books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-extra-001', 'The Cuckoo''s Calling', 'First book in the Cormoran Strike series by Robert Galbraith (J.K. Rowling).', 'Books', 'Mystery', 'Mulholland Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-extra-001', 1899)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1899;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-extra-001', 120)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 120;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-extra-002', 'The No. 1 Ladies'' Detective Agency', 'First book in Alexander McCall Smith''s charming mystery series set in Botswana.', 'Books', 'Mystery', 'Anchor')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-extra-002', 1699)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1699;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-extra-002', 130)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 130;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-extra-003', 'In the Woods', 'Psychological thriller by Tana French about a detective investigating a murder.', 'Books', 'Mystery', 'Penguin Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-extra-003', 1799)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1799;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-extra-003', 115)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 115;

-- ============================================================================
-- Add more Non-fiction books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-extra-001', 'Educated', 'Memoir by Tara Westover about growing up in a survivalist family and pursuing education.', 'Books', 'Non-fiction', 'Random House')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-extra-001', 1799)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1799;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-extra-001', 145)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 145;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-extra-002', 'The Warmth of Other Suns', 'Epic history by Isabel Wilkerson about the Great Migration of African Americans.', 'Books', 'Non-fiction', 'Vintage')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-extra-002', 2199)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 2199;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-extra-002', 125)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 125;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-extra-003', 'The Body Keeps the Score', 'Groundbreaking book by Bessel van der Kolk about trauma and healing.', 'Books', 'Non-fiction', 'Penguin Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-extra-003', 1999)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1999;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-extra-003', 140)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 140;

-- ============================================================================
-- Add more Romance books (if needed)
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-extra-001', 'The Notebook', 'Bestselling romance novel by Nicholas Sparks about enduring love.', 'Books', 'Romance', 'Grand Central Publishing')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-extra-001', 1699)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1699;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-extra-001', 150)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 150;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-extra-002', 'Me Before You', 'Emotional romance novel by Jojo Moyes about love and difficult choices.', 'Books', 'Romance', 'Penguin Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-extra-002', 1799)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 1799;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-extra-002', 140)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 140;

-- Verify final counts
SELECT 'Final book counts by subcategory:' as info;
SELECT subcategory, COUNT(*) as count 
FROM products 
WHERE category = 'Books' 
GROUP BY subcategory 
ORDER BY subcategory;
