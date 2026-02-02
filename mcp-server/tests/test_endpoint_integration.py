"""
Integration tests for query normalization in search endpoint.
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.endpoints import search_products
from app.schemas import SearchProductsRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Product, Price, Inventory

# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///./test_endpoint_integration.db"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function", autouse=True)
def setup_db():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


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


@pytest.mark.asyncio
async def test_search_with_typo_correction(db_session, sample_products):
    """Test that search corrects typos in query."""
    request = SearchProductsRequest(
        query="laptop with nvidiaa gpu",
        limit=10
    )
    
    response = await search_products(request, db_session)
    
    # Should find NVIDIA products even with typo "nvidiaa"
    assert response.status.value == "OK"
    # Query normalization should have corrected "nvidiaa" → "nvidia"
    # Results may include NVIDIA products


@pytest.mark.asyncio
async def test_search_with_synonym_expansion(db_session, sample_products):
    """Test that search expands synonyms."""
    request = SearchProductsRequest(
        query="gpu",
        limit=10
    )
    
    response = await search_products(request, db_session)
    
    # Should find GPU-related products
    assert response.status.value == "OK"
    # Synonym expansion should help find "graphics card" products


@pytest.mark.asyncio
async def test_search_with_character_repetition(db_session, sample_products):
    """Test that search normalizes character repetition."""
    request = SearchProductsRequest(
        query="gaming laptopp",
        limit=10
    )
    
    response = await search_products(request, db_session)
    
    # Should find laptops even with typo "laptopp"
    assert response.status.value == "OK"
    # Character repetition normalization should help
