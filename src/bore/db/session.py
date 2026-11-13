"""Sesiones async de SQLAlchemy para la aplicación."""

from __future__ import annotations

from collections.abc import AsyncIterator

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
    _engine = create_async_engine(database_url, pool_pre_ping=True)
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
