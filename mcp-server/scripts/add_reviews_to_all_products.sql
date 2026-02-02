-- Add reviews field to all products in the database
-- This script adds realistic user reviews as free text to every product

-- Update laptops with reviews
UPDATE products 
SET reviews = '***** "Absolutely love this laptop! The battery life is incredible and the screen is crystal clear. Runs everything smoothly. Highly recommend!" - Sarah M.

***** "Best purchase I''ve made this year. Lightweight and portable, perfect for school work. Fast boot times. Worth every penny." - John D.

**** "Great value for money. Gets a bit hot under heavy load but overall very satisfied with my purchase." - Maria L.

***** "Outstanding quality and performance. Screen is amazing and the keyboard feels great. Would definitely buy again." - David K.

**** "Good product overall. Heavier than expected but the performance makes up for it. Solid purchase." - Emily R.' 
WHERE category = 'Electronics' AND (name ILIKE '%laptop%' OR name ILIKE '%macbook%' OR name ILIKE '%thinkpad%' OR name ILIKE '%xps%' OR name ILIKE '%chromebook%');

-- Update books with reviews
UPDATE products 
SET reviews = '***** "Life-changing read! Well-written and engaging. Couldn''t put it down. Highly insightful and full of practical advice." - Alex T.

***** "Absolutely love this book! Exceeded my expectations. Full of practical advice that I''ve already started applying. Worth every penny." - Jessica W.

**** "Good book overall. Some parts were repetitive but the core message is valuable. Still worth reading." - Michael B.

***** "Five stars! This book is fantastic. Well-written and engaging. Would definitely recommend to others." - Lisa C.

**** "Decent read. Not what I expected but still found some useful insights. Overall satisfied." - Robert H.' 
WHERE category = 'Books';

-- Update vehicles (if any exist in products table)
UPDATE products 
SET reviews = '***** "Absolutely love this vehicle! Exceeded my expectations. Great fuel economy and comfortable ride. Highly recommend!" - Tom S.

***** "Best purchase I''ve made this year. Reliable and well-built. Perfect for my daily commute. Worth every penny." - Jennifer M.

**** "Good vehicle overall. Some minor issues with the infotainment system but the performance makes up for it." - Chris L.

***** "Outstanding quality and performance. Smooth ride and great handling. Would definitely buy again." - Amanda K.

**** "Solid purchase. Comfortable seats and good cargo space. Only complaint is the fuel economy could be better." - Mark R.' 
WHERE category IN ('SUV', 'Sedan', 'Truck', 'Van', 'Convertible', 'Coupe', 'Hatchback', 'Wagon');

-- For any products without reviews, add a default review
UPDATE products 
SET reviews = '***** "Great product! Good quality and value for money. Satisfied with my purchase." - Customer Review

**** "Decent product. Works as expected. Overall satisfied." - Customer Review

***** "Excellent product! Highly recommend." - Customer Review' 
WHERE reviews IS NULL OR reviews = '';
