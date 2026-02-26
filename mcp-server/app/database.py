"""
Database connection and session management.
Uses SQLAlchemy for Postgres connections.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool
import os
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL") or ""

# Base class for all our database models (must be defined before engine)
Base = declarative_base()

if DATABASE_URL:
    try:
        # Use NullPool for Supabase transaction/session pooler — the pooler
        # manages connections itself, so SQLAlchemy's client-side pool is redundant.
        engine = create_engine(DATABASE_URL, pool_pre_ping=True, poolclass=NullPool)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    except Exception as e:
        logger.warning(f"Failed to create DB engine: {e} — DB features disabled")
        engine = None
        SessionLocal = None
else:
    logger.info("DATABASE_URL not set — running without local DB (Supabase REST API will be used)")
    engine = None
    SessionLocal = None

def get_db():
    """
    Dependency function that provides a database session.
    Yields None when DATABASE_URL is not configured (DB features are skipped).
    """
    if SessionLocal is None:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
