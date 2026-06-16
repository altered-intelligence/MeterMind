"""Alembic migration environment.

Wired to the app's declarative metadata and settings, so ``alembic revision
--autogenerate`` and ``alembic upgrade head`` work against whatever ``HUI_DATABASE_URL``
points at (SQLite locally, PostgreSQL later). ``render_as_batch`` keeps ALTERs working
on SQLite.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  (register all tables on the metadata)
from app.config import get_settings
from app.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime database URL from app settings.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Render the custom Money type as ``Money()`` (imported) so migrations create the
    same per-dialect column (TEXT on SQLite) the ORM expects — not a bare Numeric, which
    would give SQLite NUMERIC affinity and coerce our exact decimal-strings to floats."""
    from app.models import Money

    if type_ == "type" and isinstance(obj, Money):
        autogen_context.imports.add("from app.models import Money")
        return "Money()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
