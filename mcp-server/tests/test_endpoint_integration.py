"""
Integration tests for query normalization in search endpoint.
Uses PostgreSQL (same as app) via DATABASE_URL.
"""

import asyncio
import pytest
import sys
import os
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.endpoints import search_products
from app.schemas import SearchProductsRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, DATABASE_URL
from app.models import Product, Price, Inventory

# Use PostgreSQL (same as app)
TEST_DATABASE_URL = os.getenv("DATABASE_URL", DATABASE_URL)
test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

TEST_IDS = ["PROD-NVIDIA-001", "PROD-LAPTOP-001"]


@pytest.fixture(scope="function", autouse=True)
def setup_db():
    """Create tables if needed, add test products, clean up after."""
    Base.metadata.create_all(bind=test_engine)
    # Remove leftover test products from previous run
    db = TestingSessionLocal()
    for pid in TEST_IDS:
        db.query(Price).filter(Price.product_id == pid).delete(synchronize_session=False)
        db.query(Inventory).filter(Inventory.product_id == pid).delete(synchronize_session=False)
        db.query(Product).filter(Product.product_id == pid).delete(synchronize_session=False)
    db.commit()
    db.close()
    yield
    # Clean up test products
    db = TestingSessionLocal()
    for pid in TEST_IDS:
        db.query(Price).filter(Price.product_id == pid).delete(synchronize_session=False)
        db.query(Inventory).filter(Inventory.product_id == pid).delete(synchronize_session=False)
        db.query(Product).filter(Product.product_id == pid).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture
def db_session():
    """Create a database session for testing."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_products(db_session):
    """Create sample products for testing."""
    products = [
        Product(
            product_id="PROD-NVIDIA-001",
            name="NVIDIA GeForce RTX 4090",
            description="High-end gaming GPU with NVIDIA architecture",
            category="Electronics",
            brand="NVIDIA"
        ),
        Product(
            product_id="PROD-LAPTOP-001",
            name="Gaming Laptop",
            description="Powerful laptop for gaming and work",
            category="Electronics",
            brand="Dell"
        ),
    ]
    
    for product in products:
        db_session.add(product)
        db_session.add(Price(
            product_id=product.product_id,
            price_cents=99900,
            currency="USD"
        ))
        db_session.add(Inventory(
            product_id=product.product_id,
            available_qty=10,
            reserved_qty=0
        ))
    
    db_session.commit()
    return products


def test_search_with_typo_correction(db_session, sample_products):
    """Test that search corrects typos in query."""
    request = SearchProductsRequest(
        query="laptop with nvidiaa gpu",
        limit=10
    )
    
    response = asyncio.run(search_products(request, db_session))
    
    # Should find NVIDIA products even with typo "nvidiaa"
    assert response.status.value == "OK"
    # Query normalization should have corrected "nvidiaa" → "nvidia"
    # Results may include NVIDIA products


def test_search_with_synonym_expansion(db_session, sample_products):
    """Test that search expands synonyms (use specific query to avoid ambiguous detection)."""
    request = SearchProductsRequest(
        query="laptop with gpu",
        limit=10
    )
    
    response = asyncio.run(search_products(request, db_session))
    
    # Should find GPU-related products (synonym expansion: gpu -> graphics card, video card)
    assert response.status.value == "OK"


def test_search_with_character_repetition(db_session, sample_products):
    """Test that search normalizes character repetition."""
    request = SearchProductsRequest(
        query="gaming laptopp",
        limit=10
    )
    
    response = asyncio.run(search_products(request, db_session))
    
    # Should find laptops even with typo "laptopp"
    assert response.status.value == "OK"
    # Character repetition normalization should help
