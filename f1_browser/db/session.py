from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from db.models import Base

DATABASE_URL = "sqlite:///./f1_world.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA wal_autocheckpoint=1000")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    # Migrations for columns added after initial schema
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE driver_season_stats ADD COLUMN effective_skill REAL"))
            conn.commit()
        except Exception:
            pass  # already exists


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session():
    """FastAPI dependency: yields a read-only DB session.

    This session is never committed on exit. Use get_session() for write routes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
