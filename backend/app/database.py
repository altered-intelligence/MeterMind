"""SQLAlchemy engine / session setup.

SQLite for the MVP; the schema is written to migrate cleanly to PostgreSQL (set
``HUI_DATABASE_URL=postgresql+psycopg://...``). Money is stored exactly (see
``models.Money``), so no Decimal precision is lost on SQLite.

``init_db()`` is fine for quick local dev; Alembic (see ``backend/alembic/``) is the
migration path for schema changes and PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

_settings = get_settings()

DEFAULT_USER_EMAIL = "local@coserv.local"

_is_sqlite = _settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(_settings.database_url, echo=False, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforce foreign keys on SQLite so ON DELETE CASCADE/SET NULL actually fire."""
    if _is_sqlite:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Initialize the schema for local dev and seed the default user.

    On SQLite we create tables directly (convenient for local dev). On other backends
    (PostgreSQL) the schema is owned by Alembic (``alembic upgrade head``) — we do NOT
    auto-create, to avoid masking migration drift.
    """
    from . import models  # noqa: F401

    if _is_sqlite:
        Base.metadata.create_all(bind=engine)

    # MVP is single-user and unauthenticated; ensure a default user exists. Tolerant if
    # the tables don't exist yet (e.g. fresh PostgreSQL before `alembic upgrade head`).
    try:
        with SessionLocal() as db:
            existing = db.query(models.User).filter_by(email=DEFAULT_USER_EMAIL).one_or_none()
            if existing is None:
                db.add(models.User(email=DEFAULT_USER_EMAIL))
                db.commit()
    except Exception:
        pass


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
