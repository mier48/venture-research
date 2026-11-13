"""Sesiones async de SQLAlchemy para la aplicación."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        # psycopg3 prepara sentencias por conexión; una intercalación entre
        # tareas async que comparten conexión provoca "prepared statement
        # _pg3_N already exists" y deja la transacción abortada. Desactivado.
        connect_args={"prepare_threshold": None},
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Motor de base de datos no inicializado")
    return _engine


async def session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Motor de base de datos no inicializado")
    async with _sessionmaker() as s:
        yield s


def new_session() -> AsyncSession:
    if _sessionmaker is None:
        raise RuntimeError("Motor de base de datos no inicializado")
    return _sessionmaker()


@asynccontextmanager
async def job_engine(
    database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Engine por trabajo: el worker ejecuta cada job con un `asyncio.run`
    (event loop nuevo). Reutilizar el pool global entre loops distintos es
    frágil: las conexiones psycopg abiertas por un loop pueden quedar
    invalidadas en el siguiente. Se crea dentro del loop y se dispone siempre."""
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
