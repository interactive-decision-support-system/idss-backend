"""
Database connection and session management.
Uses SQLAlchemy for Postgres connections.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is required. Configure apps/backend/.env with the "
        "Supabase/Postgres connection string before starting the backend."
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
        @merchant_agent.post("/endpoint")
        def my_endpoint(db: Session = Depends(get_db)):
            # use db here
            pass
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
