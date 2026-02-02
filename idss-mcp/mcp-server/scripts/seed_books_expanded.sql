-- Expanded Book Catalog with Subcategories
-- Adds 50+ books across multiple genres/subcategories

-- First, ensure subcategory column exists
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'products' AND column_name = 'subcategory'
    ) THEN
        ALTER TABLE products ADD COLUMN subcategory VARCHAR(100);

        CREATE INDEX idx_products_subcategory ON products(subcategory);

    END IF;
END $$;

-- ============================================================================
-- MYSTERY/THRILLER BOOKS - 10 books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-001', 'The Girl with the Dragon Tattoo', 'International bestseller by Stieg Larsson. A journalist and a hacker investigate a decades-old disappearance.', 'Books', 'Mystery', 'Vintage Crime/Black Lizard') ON CONFLICT (product_id) DO NOTHING;
INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-001', 1699) ON CONFLICT (product_id) DO NOTHING;
INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-001', 120) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-002', 'Gone Girl', 'Psychological thriller by Gillian Flynn about a marriage gone terribly wrong.', 'Books', 'Mystery', 'Crown Publishing') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-002', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-002', 110) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-003', 'The Silent Patient', 'Psychological thriller by Alex Michaelides about a woman who refuses to speak after allegedly murdering her husband.', 'Books', 'Mystery', 'Celadon Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-003', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-003', 100) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-004', 'Big Little Lies', 'Domestic thriller by Liane Moriarty about secrets and lies in a small coastal town.', 'Books', 'Mystery', 'Berkley') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-004', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-004', 115) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-005', 'The Hound of the Baskervilles', 'Classic Sherlock Holmes mystery by Arthur Conan Doyle.', 'Books', 'Mystery', 'Penguin Classics') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-005', 1299) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-005', 140) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-006', 'In the Woods', 'Debut novel by Tana French about a detective investigating a murder in his childhood hometown.', 'Books', 'Mystery', 'Penguin Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-006', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-006', 105) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-007', 'The Murder of Roger Ackroyd', 'Classic Agatha Christie mystery featuring Hercule Poirot.', 'Books', 'Mystery', 'William Morrow') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-007', 1499) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-007', 130) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-008', 'Sharp Objects', 'Dark psychological thriller by Gillian Flynn about a journalist investigating murders in her hometown.', 'Books', 'Mystery', 'Broadway Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-008', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-008', 110) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-009', 'The Woman in the Window', 'Psychological thriller by A.J. Finn about an agoraphobic woman who witnesses a crime.', 'Books', 'Mystery', 'William Morrow') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-009', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-009', 95) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-mystery-010', 'The Girl on the Train', 'Psychological thriller by Paula Hawkins about a woman who becomes entangled in a missing person investigation.', 'Books', 'Mystery', 'Riverhead Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-mystery-010', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-mystery-010', 105) ON CONFLICT (product_id) DO NOTHING;

-- ============================================================================
-- NON-FICTION BOOKS - 15 books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-001', 'Sapiens: A Brief History of Humankind', 'Bestselling book by Yuval Noah Harari exploring the history and impact of Homo sapiens.', 'Books', 'Non-fiction', 'Harper Perennial') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-001', 2299) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-001', 150) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-002', 'The Immortal Life of Henrietta Lacks', 'Non-fiction book by Rebecca Skloot about the woman whose cells were used for medical research.', 'Books', 'Non-fiction', 'Broadway Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-002', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-002', 140) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-003', 'Becoming', 'Memoir by Michelle Obama about her journey from childhood to the White House.', 'Books', 'Non-fiction', 'Crown') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-003', 2499) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-003', 120) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-004', 'The Body: A Guide for Occupants', 'Fascinating exploration of the human body by Bill Bryson.', 'Books', 'Non-fiction', 'Doubleday') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-004', 1999) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-004', 135) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-005', 'Born a Crime', 'Memoir by Trevor Noah about growing up in apartheid South Africa.', 'Books', 'Non-fiction', 'Spiegel & Grau') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-005', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-005', 145) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-006', 'The Right Stuff', 'Non-fiction book by Tom Wolfe about the early days of the U.S. space program.', 'Books', 'Non-fiction', 'Picador') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-006', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-006', 130) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-007', 'The Warmth of Other Suns', 'Epic history by Isabel Wilkerson about the Great Migration of African Americans.', 'Books', 'Non-fiction', 'Vintage') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-007', 2199) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-007', 125) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-008', 'Bad Blood', 'Non-fiction book by John Carreyrou about the rise and fall of Theranos.', 'Books', 'Non-fiction', 'Knopf') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-008', 1999) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-008', 140) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-009', 'The Splendid and the Vile', 'Non-fiction book by Erik Larson about Winston Churchill during the Blitz.', 'Books', 'Non-fiction', 'Crown') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-009', 2299) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-009', 115) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-010', 'The Tipping Point', 'Bestselling book by Malcolm Gladwell about how small changes can make a big difference.', 'Books', 'Non-fiction', 'Little, Brown and Company') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-010', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-010', 150) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-011', 'Outliers', 'Book by Malcolm Gladwell exploring what makes high achievers different.', 'Books', 'Non-fiction', 'Little, Brown and Company') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-011', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-011', 145) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-012', 'The Power of Now', 'Spiritual guide by Eckhart Tolle about living in the present moment.', 'Books', 'Non-fiction', 'New World Library') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-012', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-012', 160) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-013', 'Thinking, Fast and Slow', 'Bestselling book by Daniel Kahneman about the two systems of thinking.', 'Books', 'Non-fiction', 'Farrar, Straus and Giroux') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-013', 2199) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-013', 135) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-014', 'The 7 Habits of Highly Effective People', 'Classic self-help book by Stephen Covey about personal and professional effectiveness.', 'Books', 'Non-fiction', 'Free Press') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-014', 1999) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-014', 170) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-nonfic-015', 'The Art of War', 'Ancient Chinese military treatise by Sun Tzu, applicable to modern strategy.', 'Books', 'Non-fiction', 'Shambhala') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-nonfic-015', 1299) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-nonfic-015', 180) ON CONFLICT (product_id) DO NOTHING;

-- ============================================================================
-- SCI-FI BOOKS - 10 books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-001', 'The Three-Body Problem', 'Award-winning science fiction novel by Liu Cixin about humanity''s first contact with an alien civilization.', 'Books', 'Sci-Fi', 'Tor Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-001', 1999) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-001', 110) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-002', 'Neuromancer', 'Groundbreaking cyberpunk novel by William Gibson that defined the genre.', 'Books', 'Sci-Fi', 'Ace Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-002', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-002', 125) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-003', 'The Left Hand of Darkness', 'Classic science fiction novel by Ursula K. Le Guin about gender and society.', 'Books', 'Sci-Fi', 'Ace Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-003', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-003', 120) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-004', 'Hyperion', 'Science fiction epic by Dan Simmons about pilgrims on a journey to a mysterious planet.', 'Books', 'Sci-Fi', 'Spectra') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-004', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-004', 115) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-005', 'The Martian', 'Science fiction novel by Andy Weir about an astronaut stranded on Mars.', 'Books', 'Sci-Fi', 'Crown') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-005', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-005', 130) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-006', 'Foundation', 'Classic science fiction novel by Isaac Asimov about a galactic empire and psychohistory.', 'Books', 'Sci-Fi', 'Spectra') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-006', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-006', 140) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-007', 'Ender''s Game', 'Science fiction novel by Orson Scott Card about a child prodigy training to fight aliens.', 'Books', 'Sci-Fi', 'Tor Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-007', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-007', 135) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-008', 'Snow Crash', 'Cyberpunk novel by Neal Stephenson about a pizza delivery driver in a dystopian future.', 'Books', 'Sci-Fi', 'Bantam Spectra') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-008', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-008', 125) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-009', 'The Handmaid''s Tale', 'Dystopian novel by Margaret Atwood about a totalitarian society.', 'Books', 'Sci-Fi', 'Anchor') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-009', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-009', 145) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-scifi-010', 'Blade Runner: Do Androids Dream of Electric Sheep?', 'Science fiction novel by Philip K. Dick that inspired Blade Runner.', 'Books', 'Sci-Fi', 'Del Rey') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-scifi-010', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-scifi-010', 130) ON CONFLICT (product_id) DO NOTHING;

-- ============================================================================
-- FICTION BOOKS - 10 books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-001', 'The Great Gatsby', 'Classic American novel by F. Scott Fitzgerald about the Jazz Age.', 'Books', 'Fiction', 'Scribner') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-001', 1299) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-001', 200) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-002', 'To Kill a Mockingbird', 'Classic novel by Harper Lee about racial injustice in the American South.', 'Books', 'Fiction', 'Harper Perennial') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-002', 1499) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-002', 180) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-003', '1984', 'Dystopian novel by George Orwell about totalitarianism and surveillance.', 'Books', 'Fiction', 'Signet Classics') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-003', 1299) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-003', 190) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-004', 'Pride and Prejudice', 'Classic romance novel by Jane Austen about Elizabeth Bennet and Mr. Darcy.', 'Books', 'Fiction', 'Penguin Classics') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-004', 1199) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-004', 210) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-005', 'The Catcher in the Rye', 'Classic coming-of-age novel by J.D. Salinger.', 'Books', 'Fiction', 'Little, Brown and Company') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-005', 1399) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-005', 175) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-006', 'The Kite Runner', 'Novel by Khaled Hosseini about friendship and redemption in Afghanistan.', 'Books', 'Fiction', 'Riverhead Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-006', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-006', 150) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-007', 'The Book Thief', 'Novel by Markus Zusak about a young girl in Nazi Germany.', 'Books', 'Fiction', 'Knopf Books for Young Readers') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-007', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-007', 160) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-008', 'The Nightingale', 'Historical fiction novel by Kristin Hannah about two sisters in Nazi-occupied France.', 'Books', 'Fiction', 'St. Martin''s Press') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-008', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-008', 140) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-009', 'Little Fires Everywhere', 'Novel by Celeste Ng about family secrets and suburban life.', 'Books', 'Fiction', 'Penguin Press') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-009', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-009', 145) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-fiction-010', 'Where the Crawdads Sing', 'Novel by Delia Owens about a mysterious murder in a small North Carolina town.', 'Books', 'Fiction', 'G.P. Putnam''s Sons') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-fiction-010', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-fiction-010', 135) ON CONFLICT (product_id) DO NOTHING;

-- ============================================================================
-- ROMANCE BOOKS - 5 books
-- ============================================================================

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-001', 'The Seven Husbands of Evelyn Hugo', 'Bestselling novel by Taylor Jenkins Reid about a reclusive Hollywood icon.', 'Books', 'Romance', 'Atria Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-001', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-001', 150) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-002', 'It Ends with Us', 'Romance novel by Colleen Hoover about a young woman navigating love and relationships.', 'Books', 'Romance', 'Atria Books') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-002', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-002', 140) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-003', 'The Hating Game', 'Romantic comedy novel by Sally Thorne about workplace enemies who might be falling in love.', 'Books', 'Romance', 'William Morrow Paperbacks') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-003', 1699) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-003', 145) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-004', 'The Kiss Quotient', 'Romance novel by Helen Hoang about an autistic woman who hires an escort to teach her about relationships.', 'Books', 'Romance', 'Berkley') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-004', 1799) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-004', 135) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO products (product_id, name, description, category, subcategory, brand) VALUES
('prod-book-romance-005', 'Red, White & Royal Blue', 'Romance novel by Casey McQuiston about the First Son falling in love with the Prince of Wales.', 'Books', 'Romance', 'St. Martin''s Griffin') ON CONFLICT (product_id) DO NOTHING;

INSERT INTO prices (product_id, price_cents) VALUES ('prod-book-romance-005', 1899) ON CONFLICT (product_id) DO NOTHING;

INSERT INTO inventory (product_id, available_qty) VALUES ('prod-book-romance-005', 130) ON CONFLICT (product_id) DO NOTHING;
