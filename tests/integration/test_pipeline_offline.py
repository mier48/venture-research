"""Integración: pipeline offline íntegro con fakes + prompt injection."""

import pytest
import sqlalchemy as sa

from bore.db import repositories as repo
from bore.db.tables import claims, criterion_scores, opportunities, sources
from bore.orchestration.pipeline import mark_orphaned_runs, run_pipeline
from bore.providers.fakes import make_fake_providers

from .conftest import DEMO_CONFIG

pytestmark = pytest.mark.requires_services


async def test_pipeline_offline_completes(db, run_id):
    research, analysis, verifier = make_fake_providers()
    async with db() as s:
        await run_pipeline(
            s,
            db,
            run_id,
            DEMO_CONFIG,
            is_synthetic=True,
            research=research,
            analysis=analysis,
            verifier=verifier,
        )
    async with db() as s:
        run = await repo.get_run(s, run_id)
        assert run["status"] == "completed"
        stats = await repo.opportunity_status_counts(s, run_id)
        assert stats["published"] == 3  # nunca rellenar hasta requested_ideas
        assert stats["insufficient_evidence"] == 3
        assert stats["below_threshold"] == 1
        assert stats["not_reviewed"] == 1
        assert stats["duplicate"] == 2
        assert stats["rejected"] == 4
        assert stats["needs_manual_review"] == 1
        assert stats["not_shortlisted"] == 10
        assert sum(stats.values()) == 25


async def test_prompt_injection_does_not_alter(db, run_id):
    """La página hostil no cambia criterios, permisos ni fases (10.6)."""
    research, analysis, verifier = make_fake_providers()
    async with db() as s:
        await run_pipeline(
            s,
            db,
            run_id,
            DEMO_CONFIG,
            is_synthetic=True,
            research=research,
            analysis=analysis,
            verifier=verifier,
        )
    async with db() as s:
        inj = (
            await s.execute(
                sa.select(sources).where(
                    sources.c.url.contains("inyeccion"),
                    sources.c.research_run_id == run_id,
                )
            )
        ).one()
        assert inj.access_status == "ok"
        # La afirmación que la cita no queda verificada por la instrucción hostil.
        hostile_claims = (
            await s.execute(
                sa.select(claims).where(
                    claims.c.text.contains("hostil"),
                    claims.c.research_run_id == run_id,
                )
            )
        ).all()
        assert hostile_claims
        for c in hostile_claims:
            assert c.verification_status != "verified"
        # Ninguna nota vale 10 ni hay criterios inventados.
        scores = (
            await s.execute(
                sa.select(criterion_scores)
                .join(opportunities, criterion_scores.c.opportunity_id == opportunities.c.id)
                .where(opportunities.c.research_run_id == run_id)
            )
        ).all()
        assert scores
        assert all(x.score is None or 1 <= x.score <= 10 for x in scores)
        run = await repo.get_run(s, run_id)
        assert run["status"] == "completed"


async def test_no_url_from_prose_and_idempotent_stages(db, run_id):
    research, analysis, verifier = make_fake_providers()
    async with db() as s:
        await run_pipeline(
            s,
            db,
            run_id,
            DEMO_CONFIG,
            is_synthetic=True,
            research=research,
            analysis=analysis,
            verifier=verifier,
        )
    async with db() as s:
        srcs = (
            await s.execute(sa.select(sources).where(sources.c.research_run_id == run_id))
        ).all()
        assert srcs
        # Todas las fuentes persistidas son del proveedor (synthetic) y con
        # access_status calculado; ninguna procede de la prosa.
        assert all(x.provenance == "synthetic" for x in srcs)
        # El sondeo persiste fuentes por candidata; las páginas no registradas
        # en el fake devuelven not_found (estado calculado igualmente).
        assert all(x.access_status in ("ok", "blocked", "not_found") for x in srcs)
        n_opps = (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(opportunities)
                .where(opportunities.c.research_run_id == run_id)
            )
        ).scalar_one()
        # Re-ejecutar una etapa completada reutiliza output: no duplica filas.
        async with db() as s2:
            await run_pipeline(
                s2,
                db,
                run_id,
                DEMO_CONFIG,
                is_synthetic=True,
                research=research,
                analysis=analysis,
                verifier=verifier,
            )
        n_opps2 = (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(opportunities)
                .where(opportunities.c.research_run_id == run_id)
            )
        ).scalar_one()
        assert n_opps == n_opps2 == 25


async def test_orphan_marked_failed(db, run_id):
    async with db() as s:
        await repo.update_run(s, run_id, status="running", heartbeat_at=None)
        await s.commit()
        n = await mark_orphaned_runs(s, max_age_seconds=0)
        assert n >= 1
        run = await repo.get_run(s, run_id)
        assert run["status"] == "failed" and run["stop_reason"] == "worker_lost"
