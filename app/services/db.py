"""
RiskSentinel — Database Layer
Async SQLAlchemy with asyncpg.  All ORM models import Base from here.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# ---------------------------------------------------------------------------
# Engine & Session
# ---------------------------------------------------------------------------
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


# ---------------------------------------------------------------------------
# Dependency — injected into FastAPI route handlers
# ---------------------------------------------------------------------------
async def get_db() -> AsyncSession:
    """Yield a session; guarantee close on exit."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------
async def init_db():
    """Create all tables that are registered on Base.  Idempotent."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
