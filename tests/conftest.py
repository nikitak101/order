"""Test fixtures that isolate every API test in a temporary SQLite database."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def test_session_factory(tmp_path, monkeypatch) -> Generator[sessionmaker[Session], None, None]:
    """Create a fresh SQLite database and use it for the test app lifespan."""
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'pipeline_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=test_engine)
    factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    monkeypatch.setattr("app.main.engine", test_engine)
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        test_engine.dispose()


@pytest.fixture
def client(test_session_factory) -> Generator[TestClient, None, None]:
    """Provide a FastAPI client backed by the temporary SQLite database."""
    with TestClient(app) as test_client:
        yield test_client
