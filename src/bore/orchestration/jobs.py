"""Trabajos RQ: encolado, función síncrona y worker (9.4)."""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

import redis as redis_lib
from rq import Queue

from bore.config import Settings, get_settings
from bore.db import session as db_session
from bore.domain.enums import ErrorCode
from bore.providers.fakes import make_fake_providers

JOB_TTL_GRACE_SECONDS = 300


def compute_job_timeout(config: dict[str, Any]) -> int:
    """job_timeout = max_duration_seconds + 300 (9.4)."""
    return int(config["limits"]["max_duration_seconds"]) + JOB_TTL_GRACE_SECONDS


def enqueue_run(run_id: uuid.UUID, config: dict[str, Any], *, redis_url: str | None = None) -> Any:
    """Encola el trabajo. Devuelve el job de RQ."""
    settings = get_settings()
    conn = redis_lib.Redis.from_url(redis_url or settings.redis_url)
    queue = Queue("bore", connection=conn)
    return queue.enqueue(
        run_research_job,
        str(run_id),
        job_timeout=compute_job_timeout(config),
        result_ttl=0,
    )


def asyncio_run(coro: Any) -> Any:
    """psycopg async no admite ProactorEventLoop de Windows."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def run_research_job(run_id: str) -> None:
    """Función síncrona del trabajo: solo recibe run_id y hace asyncio.run (9.4)."""
    asyncio_run(_async_run(uuid.UUID(run_id)))


async def _async_run(run_id: uuid.UUID) -> None:
    settings = get_settings()
    maker = db_session._sessionmaker
    assert maker is not None
    async with maker() as session:
        run = await _get_run(session, run_id)
        assert run is not None
        config = run["config"]
        is_synthetic = run["is_synthetic"]
        research, analysis, verifier = _providers(settings, is_synthetic)
        from bore.orchestration.pipeline import run_pipeline

        await run_pipeline(
            session,
            maker,
            run_id,
            config,
            is_synthetic=is_synthetic,
            research=research,
            analysis=analysis,
            verifier=verifier,
        )


async def _get_run(session: Any, run_id: uuid.UUID) -> dict[str, Any] | None:
    from bore.db import repositories as repo

    return await repo.get_run(session, run_id)


def _providers(settings: Settings, is_synthetic: bool) -> Any:
    if is_synthetic or not settings.openai_api_key:
        return make_fake_providers()
    # Los adaptadores reales llegan en Fase 2; mientras tanto, modo offline.
    return make_fake_providers()


async def startup_maintenance() -> None:
    """Al arrancar el worker: marcar ejecuciones huérfanas (9.4)."""
    from bore.orchestration.pipeline import mark_orphaned_runs

    maker = db_session._sessionmaker
    assert maker is not None
    async with maker() as session:
        await mark_orphaned_runs(session)


def mark_enqueue_failed(run_id: uuid.UUID) -> None:
    """Marca la ejecución como failed/enqueue_failed si el encolado falla."""
    asyncio_run(_mark_failed(run_id))


async def _mark_failed(run_id: uuid.UUID) -> None:
    from bore.db import repositories as repo

    maker = db_session._sessionmaker
    assert maker is not None
    async with maker() as session:
        await repo.update_run(
            session,
            run_id,
            status="failed",
            error_code=ErrorCode.ENQUEUE_FAILED.value,
            error_message_safe="No se pudo encolar el trabajo",
        )
        await session.commit()
