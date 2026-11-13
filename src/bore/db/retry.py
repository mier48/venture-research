"""Reintento de errores transitorios de base de datos (deadlock, fallos de
serialización). psycopg envuelve el error del servidor en
`sqlalchemy.exc.OperationalError`/`DBAPIError` con `.orig`."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import psycopg.errors
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

_TRANSIENT_ERRORS = (
    psycopg.errors.DeadlockDetected,
    psycopg.errors.SerializationFailure,
)
_MAX_ATTEMPTS = 3
_BASE_DELAY_SECONDS = 0.05


def is_transient_db_error(exc: BaseException) -> bool:
    """True si el error SQLAlchemy envuelve un error transitorio de Postgres."""
    orig = getattr(exc, "orig", exc)
    return isinstance(orig, _TRANSIENT_ERRORS)


async def with_db_retry[T](
    session: AsyncSession,
    fn: Callable[[AsyncSession], Awaitable[T]],
    *,
    attempts: int = _MAX_ATTEMPTS,
) -> T:
    """Ejecuta `fn(session)` reintentando errores transitorios con backoff
    exponencial + jitter. Hace rollback de la sesión antes de cada reintento.
    Los errores no transitorios nunca se reintentan."""
    for i in range(attempts):
        try:
            return await fn(session)
        except (OperationalError, DBAPIError) as exc:
            if not is_transient_db_error(exc) or i == attempts - 1:
                raise
            await session.rollback()
            await asyncio.sleep(
                _BASE_DELAY_SECONDS * (2**i) + random.random() * _BASE_DELAY_SECONDS  # noqa: S311
            )
    raise AssertionError("unreachable")  # pragma: no cover
