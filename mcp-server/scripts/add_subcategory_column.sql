-- Migration: Add subcategory column to products table
-- This allows books to have genre subcategories (Mystery, Sci-Fi, Non-fiction, etc.)

-- Add subcategory column if it doesn't exist
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
