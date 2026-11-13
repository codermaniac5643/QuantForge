# tests/conftest.py

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantforge.database.base import Base
from quantforge.database.models import paper  # noqa: F401 — registers model with Base


TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/quantforge_test"


@pytest.fixture(scope="session")
def engine():
    """
    Create a test database engine once for the entire test session.
    Uses a separate `quantforge_test` database — never touches the real one.
    """
    _engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(_engine)   # create all tables
    yield _engine
    Base.metadata.drop_all(_engine)     # drop all tables when session ends
    _engine.dispose()


@pytest.fixture(scope="function")
def session(engine):
    """
    Provide a clean database session for each test.
    Wraps each test in a transaction that is rolled back after the test —
    so tests never affect each other, and the database stays clean.
    """
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    db_session = Session()

    yield db_session

    db_session.close()
    transaction.rollback()
    connection.close()