from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from arcadia_recommendation.infrastructure.config.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """`pool_pre_ping` costs one round trip per checkout and buys immunity to the connection a database
    restart or a proxy timeout has already closed — the alternative is one failed request per stale pooled
    connection, at the worst possible moment."""
    return create_async_engine(
        settings.database_url,
        echo=settings.sql_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """`expire_on_commit=False` because the use cases read aggregates back after committing, and an expired
    instance would issue a lazy load against a session that is already done. `autoflush=False` keeps the
    flush at the transaction boundary where the unit of work puts it."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class PostgresProbe:
    """The readiness check for persistence: one trivial statement, on a real connection from the real pool.
    Anything cheaper would pass while the pool was exhausted."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
