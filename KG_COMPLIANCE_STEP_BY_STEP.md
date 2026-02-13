# kg.txt Compliance Audit – Step-by-Step

**Scope:** kg.txt lines 21–316, newBOOKS.json, newJEWELRY.json, newLAPTOPS.json, records.json (1–4)

---

## Step 1: kg.txt Lines 21–116 – Schema (Node Labels, Relationships, Property Keys)

### 1.1 Node Labels (kg.txt lines 27–54)

| kg.txt Label | Our Implementation | Status |
|--------------|-------------------|--------|
| Accessory | ✅ create_accessory_node | ✅ |
| Author | ✅ create_book_node → Author | ✅ |
| Book | ✅ create_book_node | ✅ |
| Brand | ✅ BRANDED_BY → Brand | ✅ |
| CPU | ✅ HAS_CPU → CPU | ✅ |
| Category | ✅ IN_CATEGORY | ✅ |
| Display | ✅ HAS_DISPLAY → Display | ✅ |
| GPU | ✅ HAS_GPU → GPU | ✅ |
| Genre | ✅ BELONGS_TO_GENRE → Genre | ✅ |
| ItemType | ✅ IS_TYPE | ✅ |
| Jewelry | ✅ create_jewelry_node | ✅ |
| Laptop | ✅ create_laptop_node | ✅ |
| Manufacturer | ✅ MANUFACTURED_BY | ✅ |
| Material | ✅ MADE_OF | ✅ |
| Product | ✅ All product nodes | ✅ |
| Publisher | ✅ PUBLISHED_BY | ✅ |
| RAM | ✅ HAS_RAM → RAM | ✅ |
| Review | ✅ REVIEWS | ✅ |
| Sentiment | ✅ HAS_SENTIMENT | ✅ |
| Series | ✅ PART_OF_SERIES | ✅ |
| SessionIntent | ✅ | ✅ |
| StepIntent | ✅ | ✅ |
| Storage | ✅ HAS_STORAGE | ✅ |
| Theme | ✅ EXPLORES_THEME | ✅ |
| User | ✅ WROTE_REVIEW | ✅ |
| UserSession | ✅ | ✅ |

**Result: ✅ All node labels present**

### 1.2 Relationship Types (kg.txt lines 56–79)

| kg.txt | Our Implementation | Status |
|--------|-------------------|--------|
| BELONGS_TO_GENRE | ✅ Book → Genre | ✅ |
| BRANDED_BY | ✅ Jewelry, Accessory, Product → Brand | ✅ |
| EXPLORES_THEME | ✅ Book → Theme | ✅ |
| HAS_CPU | ✅ Laptop → CPU | ✅ |
| HAS_DISPLAY | ✅ Laptop → Display | ✅ |
| HAS_GPU | ✅ Laptop → GPU | ✅ |
| HAS_RAM | ✅ Laptop → RAM | ✅ |
| HAS_SENTIMENT | ✅ Review → Sentiment | ✅ |
| HAS_STORAGE | ✅ Laptop → Storage | ✅ |
| INSPIRED_BY | ✅ Literary connections | ✅ |
| IN_CATEGORY | ✅ Product → Category | ✅ |
| IS_TYPE | ✅ | ✅ |
| MADE_OF | ✅ Jewelry → Material | ✅ |
| MANUFACTURED_BY | ✅ Laptop → Manufacturer | ✅ |
| PART_OF_SERIES | ✅ Book → Series | ✅ |
| PUBLISHED_BY | ✅ Book → Publisher | ✅ |
| RECOMMENDED_WITH | ✅ | ✅ |
| REVIEWS | ✅ Product → Review | ✅ |
| SIMILAR_THEME | ✅ | ✅ |
| SIMILAR_TO | ✅ | ✅ |
| SUBGENRE_OF | ✅ Genre hierarchy | ✅ |
| WRITTEN_BY | ✅ Book → Author | ✅ |
| WROTE_REVIEW | ✅ User → Review | ✅ |

**Result: ✅ All relationship types present**

### 1.3 Property Keys (kg.txt lines 80–157)

kg.txt lists: available, awards, base_clock_ghz, battery_life_hours, brand, capacity_gb, category, color, comment, cores, country, created_at, description, edition, format, image_url, isbn, language, model, name, pages, price, product_id, publication_year, rating, resolution, screen_size_inches, sentiment_label, sentiment_score, subcategory, title, vram_gb, weight_kg, etc.

**Our extensions (not in kg.txt):**
- `source` – provenance for scraped products
- `scraped_from_url` – provenance for scraped products

**Result: ✅ All kg.txt properties present; we add 2 for provenance (acceptable extension)**

---

## Step 2: newBOOKS.json Structure

**Expected (from kg.txt + records):**
- Book:Product with title, isbn, format, description, language, publication_year, pages, price, product_id, name, category, image_url, available, edition
- BELONGS_TO_GENRE → Genre
- WRITTEN_BY → Author
- PUBLISHED_BY → Publisher

**newBOOKS.json sample:**
- Book:Product with title, isbn, format, description, language, publication_year, pages, price, product_id, name, category, image_url, available, edition, created_at ✅
- BELONGS_TO_GENRE → Genre (name, level, description) ✅

**Result: ✅ newBOOKS.json structure correct**

---

## Step 3: newJEWELRY.json Structure

**Expected:**
- Jewelry:Product with name, brand, price, description, image_url, subcategory, color, available
- BRANDED_BY → Brand

**newJEWELRY.json sample:**
- Jewelry:Product with name, brand, price, description, image_url, subcategory, color, category, available, created_at ✅
- BRANDED_BY → Brand ✅

**Issue:**
- "7 Carat Diamond Engagement Ring" has `"price": 500000.0` – likely wrong
- 500000 cents = $5000 (if stored as cents and not converted)
- 500000 dollars is unrealistic for a ring
- **Likely:** WooCommerce scrape stored price incorrectly (e.g. raw value without conversion)

**Result: ⚠️ Structure correct; price 500000 for one jewelry item is suspicious**

---

## Step 4: newLAPTOPS.json Structure

**Expected (from kg.txt + records):**
- Laptop:Product with screen_size_inches, weight_kg, battery_life_hours, refresh_rate_hz, price, product_id, name, model, subcategory, portability_score, category, brand, description
- MANUFACTURED_BY → Manufacturer
- HAS_CPU → CPU
- HAS_GPU → GPU (if present)
- HAS_RAM → RAM
- HAS_STORAGE → Storage
- HAS_DISPLAY → Display

**newLAPTOPS.json sample:**
- Laptop:Product with screen_size_inches, weight_kg, battery_life_hours, refresh_rate_hz, price, product_id, name, model, subcategory, portability_score, category, brand, description ✅
- MANUFACTURED_BY → Manufacturer ✅

**Issues:**
1. **Description mismatch:** "Samsung Work laptop with AMD Ryzen 5 7640HS processor... and Apple M3 Max GPU" – AMD CPU + Apple GPU is impossible
2. **Root cause:** `add_more_laptops.py` generates synthetic pairs (CPU, GPU) independently; GPU can be Apple when CPU is AMD
3. **extract_laptop_specs ignores DB:** `product.gpu_model` and `product.gpu_vendor` from PostgreSQL are **not used**; only `product.name` is parsed for GPU inference

**Result: ❌ Structure correct; data accuracy issues:**
- Synthetic descriptions can have wrong CPU/GPU combinations
- KG build ignores DB gpu_model/gpu_vendor

---

## Step 5: records.json (1–4) vs Implementation

**records.json:** Laptop → HAS_RAM → RAM (channels, expandable, capacity_gb, speed_mhz, type)  
**records (1).json:** Laptop → HAS_DISPLAY → Display (color_gamut, refresh_rate_hz, panel_type, brightness_nits, touch_screen, resolution, size_inches)  
**records (2).json:** Book nodes  
**records (3).json:** Same structure as records.json  

**Implementation:** create_laptop_node creates HAS_RAM, HAS_DISPLAY, HAS_GPU, HAS_CPU, HAS_STORAGE with correct property shapes.

**Result: ✅ records.json structure matches implementation**

---

## Step 6: kg.txt Lines 169–316 (Build Instructions)

| Instruction | Expected | Our Implementation | Status |
|-------------|----------|-------------------|--------|
| Clear existing graph | Yes | builder.clear_all_data() | ✅ |
| Create indexes/constraints | Yes | create_indexes_and_constraints() | ✅ |
| Load from PostgreSQL | All products | pg_db.query(Product).all() | ✅ |
| Laptops | 294+ (from earlier run) | 565 now | ✅ |
| Books | 500 | 500 | ✅ |
| Jewelry | 20 | 20 | ✅ |
| Accessories | 99 | 99 | ✅ |
| Other Electronics | 252+ | 252 | ✅ |
| Other categories | 647+ | 647 | ✅ |
| Genre hierarchy | Yes | create_genre_hierarchy() | ✅ |
| Reviews | Yes | create_review_relationships() | ✅ |
| Similarity | Yes | create_comparison_relationships SIMILAR_TO | ✅ |
| Literary connections | Yes | create_literary_connections() | ✅ |

**Result: ✅ Build flow matches kg.txt**

---

## Step 7: Summary of Gaps

| # | Gap | Severity | Fix |
|---|-----|----------|-----|
| 1 | **extract_laptop_specs ignores product.gpu_model, product.gpu_vendor** | High | Use DB values when present; fall back to name parsing |
| 2 | **add_more_laptops.py generates impossible CPU+GPU pairs** (e.g. AMD + Apple) | Medium | Restrict GPU selection by CPU (Apple GPU only with Apple CPU) |
| 3 | **Jewelry price 500000** (7 Carat Diamond Ring) | Medium | Check WooCommerce scrape; fix price conversion |
| 4 | **screen_size_inches vs description** | Low | extract_laptop_specs uses random.choice for screen; description may say different size – consider parsing from description |

---

## Step 8: Conclusion

**Schema compliance:** ✅ 100% – All node labels, relationships, and property keys from kg.txt are present.

**Structure compliance:** ✅ newBOOKS.json, newJEWELRY.json, newLAPTOPS.json, records.json match the expected schema.

**Data accuracy:** ⚠️ Issues:
1. KG build does not use PostgreSQL `gpu_model`/`gpu_vendor` for laptops
2. Synthetic laptop descriptions can have wrong CPU/GPU combinations
3. One jewelry item has suspicious price (500000)

**Recommendation:** Fix extract_laptop_specs to prefer `product.gpu_model` and `product.gpu_vendor` from PostgreSQL when available, and fix add_more_laptops for consistent CPU/GPU pairing.
