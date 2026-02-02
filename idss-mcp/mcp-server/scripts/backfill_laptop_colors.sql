-- Backfill color for Electronics (laptops, desktops, etc.) that have NULL color.
-- Run after add_color_and_scraped_from.sql. Safe to run multiple times.
-- Usage: psql mcp_ecommerce -f scripts/backfill_laptop_colors.sql

UPDATE products
SET color = CASE
  WHEN name ILIKE '%MacBook%' AND (name ILIKE '%Space Gray%' OR name ILIKE '%grey%') THEN 'Space Gray'
  WHEN name ILIKE '%MacBook%' AND name ILIKE '%Silver%' THEN 'Silver'
  WHEN name ILIKE '%MacBook%' AND name ILIKE '%Midnight%' THEN 'Midnight'
  WHEN name ILIKE '%MacBook%' AND name ILIKE '%Space Black%' THEN 'Space Black'
  WHEN name ILIKE '%MacBook%' THEN 'Space Gray'
  WHEN name ILIKE '%HP Spectre%' OR name ILIKE '%Spectre x360%' THEN 'Natural Silver'
  WHEN name ILIKE '%ThinkPad%' OR name ILIKE '%Lenovo ThinkPad%' THEN 'Black'
  WHEN name ILIKE '%Dell XPS%' THEN 'Platinum Silver'
  WHEN name ILIKE '%Zephyrus%' OR name ILIKE '%ROG%' THEN 'Eclipse Gray'
  WHEN name ILIKE '%Gamer Xtreme%' OR name ILIKE '%Trace MR%' OR name ILIKE '%desktop%' THEN 'Black'
  WHEN name ILIKE '%iPhone%' AND name ILIKE '%Titanium%' THEN 'Natural Titanium'
  WHEN name ILIKE '%Galaxy S24%' THEN 'Phantom Black'
  WHEN name ILIKE '%Pixel%' THEN 'Obsidian'
  WHEN name ILIKE '%AirPods%' THEN 'White'
  WHEN name ILIKE '%WH-1000XM%' OR (name ILIKE '%Sony%' AND name ILIKE '%headphone%') THEN 'Black'
  WHEN name ILIKE '%MX Master%' OR name ILIKE '%Logitech%Mouse%' THEN 'Graphite'
  ELSE 'Silver'
END
WHERE category = 'Electronics'
  AND (color IS NULL OR trim(color) = '');
