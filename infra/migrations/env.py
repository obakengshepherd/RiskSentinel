# ===========================================================================
# RiskSentinel — Alembic env.py  (async-aware)
# ===========================================================================

import asyncio
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context

# Import ORM Base so that all models are registered
from app.services.db import Base
from app.config import settings

# ── alembic.ini logging ────────────────────────────────────────────────────
config = context.config
if config.file_name is not None:
    fileConfig(config.file_name)

# Target metadata (for --autogenerate)
target_metadata = Base.metadata

# Override the sqlalchemy.url with the one from Settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline():
    """Render migrations as pure SQL (no live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    """Run migrations against a live async engine."""
    # asyncpg URL → use async engine
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # For async engines, wrap in AsyncEngine
    from sqlalchemy.ext.asyncio import create_async_engine
    async_engine = create_async_engine(settings.DATABASE_URL)

    async with async_engine.begin() as conn:
        await conn.run_sync(_do_run_migrations)

    await async_engine.dispose()


def _do_run_migrations(conn):
    context.configure(connection=conn, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
