-- Add subcategory column to products table if it doesn't exist
-- (This should already exist from books, but adding for safety)
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

-- Update existing laptops with subcategories based on their descriptions
-- School/Student Laptops
UPDATE products SET subcategory = 'School' 
WHERE category = 'Electronics' 
  AND (name ILIKE '%student%' OR name ILIKE '%ideapad%' OR name ILIKE '%aspire%' OR description ILIKE '%student%' OR description ILIKE '%school%');

-- Gaming Laptops
UPDATE products SET subcategory = 'Gaming' 
WHERE category = 'Electronics' 
  AND (name ILIKE '%gaming%' OR name ILIKE '%rog%' OR name ILIKE '%razer%' OR name ILIKE '%alienware%' OR description ILIKE '%gaming%');

-- Work/Business Laptops
UPDATE products SET subcategory = 'Work' 
WHERE category = 'Electronics' 
  AND (name ILIKE '%thinkpad%' OR name ILIKE '%business%' OR name ILIKE '%spectre%' OR description ILIKE '%business%' OR description ILIKE '%work%');

-- Creative Work Laptops
UPDATE products SET subcategory = 'Creative work' 
WHERE category = 'Electronics' 
  AND (name ILIKE '%xps%' OR name ILIKE '%macbook%' OR name ILIKE '%galaxy book%' OR description ILIKE '%creative%' OR description ILIKE '%video editing%' OR description ILIKE '%design%');

-- Set default for any remaining laptops (general purpose)
UPDATE products SET subcategory = 'Work' 
WHERE category = 'Electronics' 
  AND subcategory IS NULL 
  AND (name ILIKE '%laptop%' OR name ILIKE '%notebook%' OR name ILIKE '%macbook%');
