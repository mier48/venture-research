"""Trabajos RQ: encolado, función síncrona y worker (9.4)."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
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


async def requeue_orphaned_queued(
    session: Any, *, redis_url: str | None = None, older_than_seconds: int = 300
) -> int:
    """Ejecuciones `queued` sin trabajo RQ asociado -> reencolar UNA vez (9.4.6)."""
    from datetime import UTC, datetime

    from rq.job import Job

    from bore.db import repositories as repo

    settings = get_settings()
    conn = redis_lib.Redis.from_url(redis_url or settings.redis_url)
    threshold = datetime.fromtimestamp(datetime.now(UTC).timestamp() - older_than_seconds, tz=UTC)
    orphans = await repo.orphan_queued_runs(session, older_than=threshold)
    requeued = 0
    for run in orphans:
        if run["requeue_count"] >= 1:
            continue
        job_exists = False
        if run["rq_job_id"]:
            try:
                job = Job.fetch(run["rq_job_id"], connection=conn)
                job_exists = job.get_status() not in ("finished", "failed", "stopped")
            except Exception:
                job_exists = False
        if job_exists:
            continue
        try:
            job = enqueue_run(run["id"], run["config"], redis_url=redis_url)
        except Exception as exc:
            await repo.insert_event(
                session,
                run["id"],
                level="error",
                event_type="requeue_failed",
                stage=None,
                message_safe=f"Reencolado fallido: {exc}",
                payload=None,
            )
            continue
        await repo.update_run(
            session,
            run["id"],
            rq_job_id=job.id,
            requeue_count=run["requeue_count"] + 1,
        )
        await repo.insert_event(
            session,
            run["id"],
            level="warning",
            event_type="run_requeued",
            stage=None,
            message_safe="Ejecución en cola huérfana reencolada (una sola vez)",
            payload={"rq_job_id": job.id},
        )
        requeued += 1
    await session.commit()
    return requeued


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
        from bore.db import repositories as repo
        from bore.providers.errors import ProviderError

        try:
            research, analysis, verifier, provider_name, model_by_role = _providers(
                settings, is_synthetic
            )
        except ProviderError as exc:
            # La configuración real inválida marca la ejecución como fallida,
            # no la deja en cola para siempre.
            await repo.update_run(
                session,
                run_id,
                status="failed",
                error_code=exc.error_code.value,
                error_message_safe=str(exc)[:500],
            )
            await session.commit()
            return
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
            provider_name=provider_name,
            model_by_role=model_by_role,
        )


async def _get_run(session: Any, run_id: uuid.UUID) -> dict[str, Any] | None:
    from bore.db import repositories as repo

    return await repo.get_run(session, run_id)


def _providers(settings: Settings, is_synthetic: bool) -> Any:
    """Devuelve (research, analysis, verifier, provider_name, model_by_role)."""
    if is_synthetic:
        return (*make_fake_providers(), "fake", {})
    from bore.agents.factory import ROLE_MODEL_ENV, model_for_role
    from bore.providers.errors import ProviderError
    from bore.providers.http_verifier import HttpSourceVerifier
    from bore.providers.openai_analysis import OpenAIAnalysisProvider
    from bore.providers.openai_research import OpenAIResearchProvider, missing_real_mode_config

    problems = missing_real_mode_config(settings)
    if problems:
        raise ProviderError("Modo real mal configurado: " + "; ".join(problems))
    models = {
        "research": settings.bore_model_research,
        "analysis": settings.bore_model_analysis,
        "review": settings.bore_model_review,
    }
    model_by_role = {role: model_for_role(role, models) for role in ROLE_MODEL_ENV}
    verifier = HttpSourceVerifier(
        user_agent=settings.verifier_user_agent,
        timeout_seconds=settings.verifier_timeout_seconds,
        max_bytes=settings.verifier_max_bytes,
        max_redirects=settings.verifier_max_redirects,
        respect_robots=settings.verifier_respect_robots,
        per_host_min_interval=settings.verifier_per_host_min_interval,
    )
    return (
        OpenAIResearchProvider(settings),
        OpenAIAnalysisProvider(settings),
        verifier,
        "openai",
        model_by_role,
    )


async def startup_maintenance() -> None:
    """Mantenimiento del worker: huérfanas `running` -> failed (9.4.5) y
    `queued` sin job -> reencolar una vez (9.4.6)."""
    from bore.orchestration.pipeline import mark_orphaned_runs

    settings = get_settings()
    maker = db_session._sessionmaker
    assert maker is not None
    async with maker() as session:
        await mark_orphaned_runs(session, max_age_seconds=settings.bore_heartbeat_stale_seconds)
        await requeue_orphaned_queued(
            session,
            redis_url=settings.redis_url,
            older_than_seconds=settings.bore_requeue_after_seconds,
        )


def reaper_loop(stop: threading.Event) -> None:
    """Hilo demonio: repite el mantenimiento cada BORE_REAPER_INTERVAL_SECONDS."""
    settings = get_settings()
    while not stop.is_set():
        with contextlib.suppress(Exception):
            asyncio_run(startup_maintenance())
        stop.wait(settings.bore_reaper_interval_seconds)


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
