-- Add books in $30-$50 price range (3000-5000 cents)
-- This script adds books with prices between $30 and $50 to support the book interview price filtering

-- Non-fiction books in $30-$50 range
INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-001', 'The Innovator''s Dilemma', 'Groundbreaking book on disruptive innovation and why successful companies fail. Essential reading for business leaders.', 'Books', 'Non-fiction', 'Harvard Business Review Press')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-001', 3499)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3499;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-001', 85)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 85;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-002', 'Sapiens: A Brief History of Humankind', 'Bestselling exploration of how Homo sapiens conquered the world. A thought-provoking journey through human history.', 'Books', 'Non-fiction', 'Harper')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-002', 3299)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3299;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-002', 120)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 120;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-003', 'Thinking, Fast and Slow', 'Nobel Prize winner Daniel Kahneman''s groundbreaking work on the two systems that drive human thought.', 'Books', 'Non-fiction', 'Farrar, Straus and Giroux')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-003', 3899)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3899;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-003', 95)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 95;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-004', 'The Lean Startup', 'How today''s entrepreneurs use continuous innovation to create radically successful businesses.', 'Books', 'Non-fiction', 'Crown Business')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-004', 3199)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3199;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-004', 110)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 110;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-005', 'The 7 Habits of Highly Effective People', 'Powerful lessons in personal change. A comprehensive guide to personal and professional effectiveness.', 'Books', 'Non-fiction', 'Free Press')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-005', 3599)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3599;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-005', 150)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 150;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-006', 'Good to Great', 'Why some companies make the leap and others don''t. A management classic on building great companies.', 'Books', 'Non-fiction', 'HarperBusiness')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-006', 3399)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3399;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-006', 100)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 100;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-007', 'The Art of War', 'Ancient Chinese military treatise that has become a guide for business strategy and personal achievement.', 'Books', 'Non-fiction', 'Shambhala')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-007', 3099)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3099;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-007', 200)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 200;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-008', 'Outliers: The Story of Success', 'Malcolm Gladwell''s examination of what makes high-achievers different. A fascinating look at success.', 'Books', 'Non-fiction', 'Little, Brown and Company')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-008', 3299)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3299;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-008', 130)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 130;

-- Fiction books in $30-$50 range
INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-009', 'The Great Gatsby', 'F. Scott Fitzgerald''s masterpiece about the Jazz Age, love, and the American Dream. A literary classic.', 'Books', 'Fiction', 'Scribner')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-009', 3199)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3199;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-009', 180)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 180;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-010', '1984', 'George Orwell''s dystopian masterpiece about totalitarianism, surveillance, and the power of truth.', 'Books', 'Fiction', 'Signet Classics')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-010', 3099)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3099;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-010', 250)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 250;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-011', 'To Kill a Mockingbird', 'Harper Lee''s Pulitzer Prize-winning novel about racial injustice and childhood innocence in the American South.', 'Books', 'Fiction', 'Harper Perennial')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-011', 3499)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3499;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-011', 160)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 160;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-012', 'The Catcher in the Rye', 'J.D. Salinger''s controversial coming-of-age novel about teenage rebellion and alienation.', 'Books', 'Fiction', 'Little, Brown and Company')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-012', 3299)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3299;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-012', 140)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 140;

-- Sci-Fi books in $30-$50 range
INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-013', 'Dune', 'Frank Herbert''s epic science fiction masterpiece set on the desert planet Arrakis. A complex tale of politics, religion, and ecology.', 'Books', 'Sci-Fi', 'Ace Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-013', 3999)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3999;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-013', 90)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 90;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-014', 'The Foundation Trilogy', 'Isaac Asimov''s groundbreaking science fiction series about psychohistory and the fall and rise of galactic empires.', 'Books', 'Sci-Fi', 'Del Rey')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-014', 4299)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 4299;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-014', 75)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 75;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-015', 'Neuromancer', 'William Gibson''s cyberpunk classic that defined the genre. A thrilling tale of hackers, AI, and virtual reality.', 'Books', 'Sci-Fi', 'Ace Books')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-015', 3699)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3699;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-015', 85)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 85;

-- Mystery books in $30-$50 range
INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-016', 'The Girl with the Dragon Tattoo', 'Stieg Larsson''s international bestseller about a journalist and a hacker investigating a decades-old disappearance.', 'Books', 'Mystery', 'Vintage Crime')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-016', 3499)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3499;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-016', 110)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 110;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-017', 'Gone Girl', 'Gillian Flynn''s psychological thriller about a marriage gone wrong and a missing wife. A twist-filled page-turner.', 'Books', 'Mystery', 'Crown')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-017', 3299)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3299;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-017', 125)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 125;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-30-018', 'The Big Sleep', 'Raymond Chandler''s hard-boiled detective novel featuring private eye Philip Marlowe. A classic of the genre.', 'Books', 'Mystery', 'Vintage Crime')
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-30-018', 3199)
ON CONFLICT (product_id) DO UPDATE SET price_cents = 3199;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-30-018', 95)
ON CONFLICT (product_id) DO UPDATE SET available_qty = 95;

-- Verify the books were added
SELECT 
    p.product_id,
    p.name,
    p.subcategory,
    pr.price_cents / 100.0 AS price_dollars,
    i.available_qty
FROM products p
JOIN prices pr ON p.product_id = pr.product_id
JOIN inventory i ON p.product_id = i.product_id
WHERE p.category = 'Books' 
  AND pr.price_cents >= 3000 
  AND pr.price_cents <= 5000
ORDER BY pr.price_cents;
