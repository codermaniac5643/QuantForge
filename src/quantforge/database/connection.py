# src/quantforge/database/connection.py

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from quantforge.config.settings import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,     # drops stale connections before using them
    pool_size=5,
    max_overflow=10,
    echo=settings.quantforge_env == "development",  # log SQL in dev only
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Provide a transactional database session.
    Commits on success, rolls back on any exception.
    Always closes the session when done.

    Usage:
        with get_session() as session:
            session.add(record)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    """Returns True if the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False