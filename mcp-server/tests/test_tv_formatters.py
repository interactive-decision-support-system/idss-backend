from app.formatters import format_product

def test_format_tv_product_basic():
    raw = {
        "id": "tv-001",
        "title": "Samsung OLED 4K TV",
        "brand": "Samsung",
        "price": 1500,
        "product_type": "tv",
        "category": "electronics",
        "imageurl": "https://example.com/tv.jpg",
        "attributes": {
            "screen_size": 65,
            "resolution": "3840x2160",
            "panel_type": "OLED",
            "smart_platform": "Tizen",
        },
    }
    result = format_product(raw, "tvs")
    assert result.productType.value == "tv"
    assert result.tv is not None
    assert result.tv.specs.panel_type == "OLED"
    assert result.tv.specs.resolution == "3840x2160"
    assert result.tv.specs.smart_platform == "Tizen"
    assert '65"' in result.tv.specs.screen_size

def test_format_tv_missing_attrs():
    raw = {
        "id": "tv-002",
        "title": "Basic TV",
        "price": 200,
        "product_type": "tv",
        "category": "electronics",
        "attributes": {},
    }
    result = format_product(raw, "tvs")
    assert result.productType.value == "tv"
    assert result.tv is not None
    assert result.tv.specs.panel_type is None
    assert result.tv.specs.screen_size is None

def test_format_tv_placeholder_image():
    raw = {
        "id": "tv-003",
        "title": "No Image TV",
        "price": 300,
        "product_type": "tv",
        "category": "electronics",
        "attributes": {},
    }
    result = format_product(raw, "tvs")
    assert "placehold" in (result.image.primary or "")
    assert "TV" in (result.image.primary or "")

def test_format_tv_does_not_set_laptop_details():
    raw = {
        "id": "tv-004",
        "title": "LG OLED TV",
        "price": 2000,
        "product_type": "tv",
        "category": "electronics",
        "attributes": {"panel_type": "OLED"},
    }
    result = format_product(raw, "tvs")
    assert result.laptop is None
    assert result.tv is not None
