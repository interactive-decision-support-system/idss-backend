-- Logistics/shipping for orders (week4notes.txt: shipping time, cost, location).
-- Data may be synthetic at first; supports location-dependent delivery.

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS shipping_method VARCHAR(50),
  ADD COLUMN IF NOT EXISTS estimated_delivery_days INTEGER,
  ADD COLUMN IF NOT EXISTS shipping_cost_cents INTEGER,
  ADD COLUMN IF NOT EXISTS shipping_region VARCHAR(20);

COMMENT ON COLUMN orders.shipping_method IS 'e.g. standard, express';
COMMENT ON COLUMN orders.estimated_delivery_days IS 'Estimated days to delivery';
COMMENT ON COLUMN orders.shipping_cost_cents IS 'Shipping cost in cents';
COMMENT ON COLUMN orders.shipping_region IS 'Region/country code (e.g. US, EU)';
