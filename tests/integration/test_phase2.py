"""Integración Fase 2: timeout de job, reintentos, fallo de proveedor, corte
de presupuesto, cancelación, reaper/reencolado y estados finales."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from bore.db import repositories as repo
from bore.db.tables import opportunities, provider_calls
from bore.orchestration.jobs import compute_job_timeout, requeue_orphaned_queued
from bore.orchestration.pipeline import run_pipeline
from bore.providers.errors import ProviderError, ProviderServerError
from bore.providers.fakes import make_fake_providers

from .conftest import DEMO_CONFIG, REDIS_URL

pytestmark = pytest.mark.requires_services

_TERMINAL_ALLOWED = {
    "published",
    "duplicate",
    "rejected",
    "needs_manual_review",
    "not_shortlisted",
    "insufficient_evidence",
    "below_threshold",
    "not_reviewed",
    "scored",
}


def test_job_timeout_above_max_duration():
    """job_timeout = max_duration_seconds + 300 (9.4.1)."""
    assert compute_job_timeout(DEMO_CONFIG) == DEMO_CONFIG["limits"]["max_duration_seconds"] + 300


async def _run(db, run_id, config=None, **kw):
    research, analysis, verifier = make_fake_providers(**kw)
    async with db() as s:
        await run_pipeline(
            s,
            db,
            run_id,
            config or DEMO_CONFIG,
            is_synthetic=True,
            research=research,
            analysis=analysis,
            verifier=verifier,
        )


async def test_transient_provider_error_retried(db, run_id):
    failures = {"sector_mapper": [ProviderServerError("503 de ejemplo")]}
    await _run(db, run_id, failures=failures)
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "completed"


async def test_permanent_provider_error_fails_run(db, run_id):
    failures = {"idea_generator": [ProviderError("fallo permanente de ejemplo")]}
    await _run(db, run_id, failures=failures)
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "failed"
        assert run["error_code"] == "provider_error"


async def test_budget_cut_is_partial_and_stops_calls(db, run_id):
    config = deepcopy(DEMO_CONFIG)
    config["limits"]["max_model_calls"] = 4
    await _run(db, run_id, config=config)
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "partial"
        assert run["stop_reason"] == "model_call_quota_exceeded"
        calls = (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(provider_calls)
                .where(provider_calls.c.research_run_id == run_id)
            )
        ).scalar_one()
        assert calls <= 4


async def test_cooperative_cancel(db, run_id):
    async with db() as s:
        await repo.request_cancel(s, run_id)
        await s.commit()
    await _run(db, run_id)
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "cancelled"
        assert run["stop_reason"] == "cancelled_by_user"


async def test_reviewed_without_final_goes_insufficient(db, run_id):
    # idx 5 está en la lista de revisión; su nota final omite un criterio.
    await _run(db, run_id, omit_final_criteria=frozenset({5}))
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "completed"
        opps = (
            await s.execute(
                sa.select(opportunities).where(
                    opportunities.c.research_run_id == run_id,
                    opportunities.c.name == "Ejemplo de Oportunidad 5",
                )
            )
        ).one()
        assert opps.status == "insufficient_evidence"


async def test_qualified_not_published_and_terminal_invariants(db, run_id):
    config = deepcopy(DEMO_CONFIG)
    config["requested_ideas"] = 1
    await _run(db, run_id, config=config)
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "completed"
        stats = await repo.opportunity_status_counts(s, run_id)
        assert stats["published"] == 1
        # Cualificadas que exceden requested_ideas: quedan en `scored`.
        assert stats["scored"] == stats.get("scored", 0) >= 1
        stage_output = run["status"]
        assert stage_output == "completed"
        assert not any(s in stats for s in ("candidate", "eligible", "shortlisted"))
        assert stats.get("scored", 0) >= 1


async def test_orphan_queued_requeued_once(db, run_id):
    async with db() as s:
        old = datetime.now(UTC) - timedelta(seconds=3600)
        await repo.update_run(s, run_id, status="queued", created_at=old)
        await s.commit()
        n = await requeue_orphaned_queued(s, redis_url=REDIS_URL, older_than_seconds=300)
        assert n == 1
        run = await repo.get_run(s, run_id)
        assert run["requeue_count"] == 1 and run["rq_job_id"]
        # Segunda pasada: no se reencola dos veces.
        n2 = await requeue_orphaned_queued(s, redis_url=REDIS_URL, older_than_seconds=0)
        assert n2 == 0
        run = await repo.get_run(s, run_id)
        assert run["requeue_count"] == 1
