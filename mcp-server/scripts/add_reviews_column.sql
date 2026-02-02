-- Add reviews column to products if it doesn't exist (matches app/models.py Product.reviews)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'reviews'
  ) THEN
    ALTER TABLE products ADD COLUMN reviews TEXT;
  END IF;
END $$;
