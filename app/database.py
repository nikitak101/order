"""SQLAlchemy engine, session factory, and declarative model base."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all database models."""


def get_db() -> Generator[Session, None, None]:
    """Provide a database session for one request and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
