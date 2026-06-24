"""
Shared SQLAlchemy engine and session factory.

Uses PostgreSQL (Supabase) when DATABASE_URL is set, otherwise SQLite.
Tables are created on init_db() if they do not exist.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config

_db_initialized = False


class Base(DeclarativeBase):
    pass


def _engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(config.DATABASE_URL, echo=False, **_engine_kwargs(config.DATABASE_URL))
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Session:
    return SessionLocal()


def init_db() -> None:
    """Create all registered tables if they do not exist."""
    global _db_initialized
    if _db_initialized:
        return

    import portfolio  # noqa: F401 — register Position model
    import alerts  # noqa: F401 — register Alert model

    Base.metadata.create_all(bind=engine)
    _db_initialized = True
    backend = "PostgreSQL" if config.DATABASE_URL.startswith("postgresql") else "SQLite"
    print(f"Database ready ({backend})")
