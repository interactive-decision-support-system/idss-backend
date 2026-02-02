"""
Database connection and session management.
Uses SQLAlchemy for Postgres connections.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Get database URL from environment, with sensible default for local development
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://julih@localhost:5432/mcp_ecommerce"
)

# Create database engine
# pool_pre_ping ensures connections are alive before using them
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# Create session factory
# Session is the gateway to interact with the database
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all our database models
Base = declarative_base()


def get_db():
    """
    Dependency function that provides a database session.
    Automatically closes the session after the request is done.
    
    Usage in FastAPI:
        @app.post("/endpoint")
        def my_endpoint(db: Session = Depends(get_db)):
            # use db here
            pass
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
