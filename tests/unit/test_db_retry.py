"""with_db_retry: reintenta errores transitorios (deadlock, serialization)
con rollback + backoff; no reintenta errores no transitorios."""

import psycopg.errors
import pytest
from sqlalchemy.exc import OperationalError

from bore.db.retry import is_transient_db_error, with_db_retry


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def _deadlock() -> OperationalError:
    return OperationalError("INSERT ...", {}, psycopg.errors.DeadlockDetected("deadlock"))


async def test_retries_deadlock_then_succeeds():
    session = _Session()
    calls = 0

    async def fn(_s):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _deadlock()
        return "ok"

    assert await with_db_retry(session, fn) == "ok"  # type: ignore[arg-type]
    assert calls == 2
    assert session.rollbacks == 1


async def test_non_transient_error_is_not_retried():
    session = _Session()
    calls = 0

    async def fn(_s):
        nonlocal calls
        calls += 1
        raise OperationalError("INSERT ...", {}, ValueError("not transient"))

    with pytest.raises(OperationalError):
        await with_db_retry(session, fn)  # type: ignore[arg-type]
    assert calls == 1
    assert session.rollbacks == 0


async def test_transient_error_exhausts_attempts():
    session = _Session()
    calls = 0

    async def fn(_s):
        nonlocal calls
        calls += 1
        raise _deadlock()

    with pytest.raises(OperationalError):
        await with_db_retry(session, fn)  # type: ignore[arg-type]
    assert calls == 3
    assert session.rollbacks == 2


def test_is_transient_db_error():
    assert is_transient_db_error(_deadlock())
    assert is_transient_db_error(
        OperationalError("s", {}, psycopg.errors.SerializationFailure("x"))
    )
    assert not is_transient_db_error(OperationalError("s", {}, ValueError("x")))
    assert not is_transient_db_error(ValueError("x"))
