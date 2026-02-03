# Frontend Integration Plan: Unified Product Display

## Overview

The MCP backend serves three product types: **Vehicles**, **Laptops/Electronics**, and **Books**. Currently, the frontend was built for the IDSS vehicle format, and non-vehicle products are "hacked" to fit that format. This plan describes how to properly support all product types.

## Current State

### IDSS Vehicle Format (What frontend expects)
```json
{
  "@id": "unified/VIN123",
  "vin": "3CZRZ1H39TM727005",
  "online": true,
  "vehicle": {
    "year": 2026,
    "make": "Honda",
    "model": "HR-V",
    "trim": "LX",
    "price": 27650,
    "mileage": null,
    "bodyStyle": "SUV",
    "drivetrain": "FWD",
    "engine": "1.5L Turbocharged",
    "fuel": "Unleaded",
    "transmission": "CVT",
    "doors": 4,
    "seats": 5,
    "exteriorColor": "Pearl White",
    "build_city_mpg": 28,
    "build_highway_mpg": 33
  },
  "retailListing": {
    "price": 27650,
    "dealer": "Honda World Seattle",
    "city": "Seattle",
    "state": "WA",
    "primaryImage": "https://images.example.com/photo.jpg",
    "photoCount": 12,
    "vdp": "https://dealer.com/vehicle/...",
    "carfaxUrl": "https://carfax.com/...",
    "used": false,
    "cpo": false
  }
}
```

### Current Hack for Products
Products are mapped to vehicle fields awkwardly:
- `make` = brand
- `model` = product name
- `drivetrain` = "Laptop" or "Book"
- `bodyStyle` = "Electronics" or "Books"
- All vehicle-specific fields (doors, seats, mpg) = 0

## Proposed Solution: Unified Product Schema

### Option A: Polymorphic Response (Recommended)

Add a `productType` field to identify the type, with type-specific details in dedicated objects.

```json
{
  // Common fields for ALL product types
  "id": "product-123",
  "productType": "vehicle" | "laptop" | "book",
  "name": "Honda HR-V LX",
  "brand": "Honda",
  "price": 27650,
  "currency": "USD",
  "image": {
    "primary": "https://...",
    "count": 12,
    "gallery": ["https://...", "https://..."]
  },
  "url": "https://...",  // Product detail page
  "available": true,

  // Type-specific details (only one will be present)
  "vehicle": { ... },    // Present if productType === "vehicle"
  "laptop": { ... },     // Present if productType === "laptop"
  "book": { ... },       // Present if productType === "book"

  // Legacy compatibility (can be removed after frontend migration)
  "retailListing": { ... }
}
```

### Type-Specific Details

#### Vehicle Details
```json
"vehicle": {
  "vin": "3CZRZ1H39TM727005",
  "year": 2026,
  "model": "HR-V",
  "trim": "LX",
  "bodyStyle": "SUV",
  "drivetrain": "FWD",
  "engine": "1.5L Turbocharged",
  "fuel": "Unleaded",
  "transmission": "CVT",
  "doors": 4,
  "seats": 5,
  "mileage": null,
  "exteriorColor": "Pearl White",
  "interiorColor": "Black",
  "mpg": { "city": 28, "highway": 33 },
  "condition": "new",
  "cpo": false,
  "dealer": {
    "name": "Honda World Seattle",
    "city": "Seattle",
    "state": "WA",
    "zip": "98101"
  },
  "carfaxUrl": "https://..."
}
```

#### Laptop/Electronics Details
```json
"laptop": {
  "productType": "gaming_laptop",
  "specs": {
    "processor": "Intel Core i7-13700H",
    "ram": "16GB DDR5",
    "storage": "512GB NVMe SSD",
    "display": "15.6\" FHD 144Hz",
    "graphics": "NVIDIA RTX 4060"
  },
  "gpuVendor": "NVIDIA",
  "gpuModel": "RTX 4060",
  "color": "Black",
  "tags": ["gaming", "portable"]
}
```

#### Book Details
```json
"book": {
  "author": "Stephen King",
  "genre": "Horror",
  "format": "Hardcover",
  "pages": 450,
  "isbn": "978-1234567890",
  "publisher": "Scribner",
  "publishedDate": "2024-03-15",
  "language": "English"
}
```

---

## Frontend Implementation Plan

### Phase 1: Add Product Type Detection (Non-Breaking)

**Files to modify:**
- Product card component
- Product grid component
- Product detail modal/page

**Changes:**
```typescript
// Add product type detection
function getProductType(product: any): 'vehicle' | 'laptop' | 'book' {
  if (product.productType) {
    return product.productType;  // New format
  }
  // Legacy detection
  if (product.vehicle?.bodyStyle === 'Electronics') return 'laptop';
  if (product.vehicle?.bodyStyle === 'Books') return 'book';
  return 'vehicle';
}
```

### Phase 2: Create Type-Specific Card Components

Create separate card components for each product type:

#### VehicleCard
```tsx
<VehicleCard
  image={product.image.primary}
  year={product.vehicle.year}
  make={product.brand}
  model={product.vehicle.model}
  trim={product.vehicle.trim}
  price={product.price}
  mileage={product.vehicle.mileage}
  bodyStyle={product.vehicle.bodyStyle}
  fuel={product.vehicle.fuel}
  dealer={product.vehicle.dealer.name}
  location={`${product.vehicle.dealer.city}, ${product.vehicle.dealer.state}`}
/>
```

#### LaptopCard
```tsx
<LaptopCard
  image={product.image.primary}
  name={product.name}
  brand={product.brand}
  price={product.price}
  processor={product.laptop.specs.processor}
  ram={product.laptop.specs.ram}
  storage={product.laptop.specs.storage}
  display={product.laptop.specs.display}
  graphics={product.laptop.specs.graphics}
  tags={product.laptop.tags}
/>
```

#### BookCard
```tsx
<BookCard
  image={product.image.primary}
  title={product.name}
  author={product.book.author}
  price={product.price}
  genre={product.book.genre}
  format={product.book.format}
  pages={product.book.pages}
/>
```

### Phase 3: Update Product Grid

```tsx
function ProductCard({ product }) {
  const type = getProductType(product);

  switch (type) {
    case 'vehicle':
      return <VehicleCard product={product} />;
    case 'laptop':
      return <LaptopCard product={product} />;
    case 'book':
      return <BookCard product={product} />;
    default:
      return <GenericProductCard product={product} />;
  }
}
```

### Phase 4: Update Bucket/Row Display

The bucket labels work for all product types, but the row headers could be customized:

```tsx
function BucketHeader({ label, dimension, productType }) {
  // Icons based on dimension
  const icons = {
    price: '$',
    fuel_type: '⛽',
    make: '🏭',
    body_style: '🚗',
    genre: '📚',
    brand: '🏷️'
  };

  return (
    <div className="bucket-header">
      <span className="bucket-icon">{icons[dimension] || ''}</span>
      <span className="bucket-label">{label}</span>
    </div>
  );
}
```

---

## Backend Changes (MCP)

### 1. Update `_format_product_as_vehicle()` to use new format

```python
def format_product(product: Dict, category: str) -> Dict:
    """Format product in unified schema."""

    # Determine product type
    if category == "Electronics":
        product_type = "laptop"
    elif category == "Books":
        product_type = "book"
    else:
        product_type = "generic"

    base = {
        "id": product["product_id"],
        "productType": product_type,
        "name": product["name"],
        "brand": product.get("brand", ""),
        "price": product["price"],
        "currency": "USD",
        "image": {
            "primary": product.get("image_url", ""),
            "count": 1 if product.get("image_url") else 0,
            "gallery": []
        },
        "url": "",
        "available": True,
    }

    # Add type-specific details
    if product_type == "laptop":
        base["laptop"] = {
            "productType": product.get("product_type", "laptop"),
            "specs": {
                "processor": "",  # Extract from description if available
                "ram": "",
                "storage": "",
                "display": "",
                "graphics": product.get("gpu_model", "")
            },
            "gpuVendor": product.get("gpu_vendor", ""),
            "gpuModel": product.get("gpu_model", ""),
            "color": product.get("color", ""),
            "tags": product.get("tags", [])
        }
    elif product_type == "book":
        base["book"] = {
            "author": "",  # Would need to extract from description
            "genre": product.get("subcategory", ""),
            "format": "",
            "pages": None,
            "isbn": "",
            "publisher": "",
            "language": "English"
        }

    # Legacy compatibility - keep retailListing for now
    base["retailListing"] = {
        "price": product["price"],
        "primaryImage": product.get("image_url", ""),
        "photoCount": 1 if product.get("image_url") else 0,
    }

    # Legacy vehicle object for backwards compatibility
    base["vehicle"] = {
        "make": product.get("brand", ""),
        "model": product["name"],
        "price": product["price"],
        "bodyStyle": category,
    }

    return base
```

### 2. Vehicle format already correct

The IDSS vehicle format is already comprehensive. Just ensure the new `productType` field is added:

```python
vehicle_payload["productType"] = "vehicle"
```

---

## API Response Changes

### Current ChatResponse
```json
{
  "response_type": "recommendations",
  "message": "Here are your recommendations...",
  "recommendations": [[...], [...], [...]],
  "bucket_labels": ["Budget", "Mid-Range", "Premium"],
  "diversification_dimension": "price",
  "domain": "vehicles"
}
```

### Enhanced ChatResponse
```json
{
  "response_type": "recommendations",
  "message": "Here are your recommendations...",
  "recommendations": [[...], [...], [...]],
  "bucket_labels": ["Budget", "Mid-Range", "Premium"],
  "diversification_dimension": "price",
  "domain": "vehicles",
  "productType": "vehicle",  // NEW: explicit product type
  "displayConfig": {         // NEW: frontend display hints
    "cardType": "vehicle",
    "showMileage": true,
    "showDealer": true,
    "showMpg": true,
    "priceLabel": "MSRP"
  }
}
```

---

## Image Handling

### Current State
- Vehicles: Images from auto.dev stored in SQLite `primary_image_url`
- Products: Images in PostgreSQL `products.image_url` (may be NULL for many products)

### Recommendations

1. **Placeholder Images**: Add default placeholder images for products without images
   ```
   /assets/placeholders/laptop.png
   /assets/placeholders/book.png
   /assets/placeholders/product.png
   ```

2. **Image Proxy**: Consider proxying images through your server to:
   - Add caching
   - Handle CORS issues
   - Provide consistent sizing

3. **Lazy Loading**: Implement lazy loading for images in the grid

---

## Migration Steps

### Backend (This PR)
1. [x] Vehicles already use correct format
2. [ ] Update `format_product()` to include `productType` field
3. [ ] Add type-specific detail objects (`laptop`, `book`)
4. [ ] Keep legacy `vehicle` and `retailListing` for backwards compatibility
5. [ ] Add `displayConfig` hints to response

### Frontend (Separate PR)
1. [ ] Add `getProductType()` utility function
2. [ ] Create `LaptopCard` component
3. [ ] Create `BookCard` component
4. [ ] Update `ProductCard` to use type-specific components
5. [ ] Add placeholder images
6. [ ] Test with all three product types
7. [ ] Remove legacy fallbacks once stable

---

## Testing Checklist

### Vehicles
- [ ] Image displays correctly
- [ ] Year, Make, Model, Trim show correctly
- [ ] Price formatted correctly
- [ ] Mileage shows (or "New" for 0 mileage)
- [ ] Fuel type, body style badges work
- [ ] Dealer info displays
- [ ] Click opens detail view

### Laptops
- [ ] Image displays (or placeholder)
- [ ] Brand, Name show correctly
- [ ] Price formatted correctly
- [ ] Key specs display (if available)
- [ ] Tags/badges work
- [ ] Click opens detail view

### Books
- [ ] Cover image displays (or placeholder)
- [ ] Title, Author show correctly
- [ ] Price formatted correctly
- [ ] Genre badge works
- [ ] Format shows (Hardcover/Paperback/etc)
- [ ] Click opens detail view

---

## Questions for Frontend Team

1. **Image sizing**: What dimensions should product images be? (Current vehicle images are various sizes)

2. **Detail modal**: Should clicking a product open:
   - A modal with more details?
   - A new page?
   - An external link (for vehicles, the VDP)?

3. **Compare feature**: Is there a need to compare products across types?

4. **Favorites/wishlist**: Should users be able to save products?

5. **Mobile layout**: How should the 3x3 grid adapt on mobile?
