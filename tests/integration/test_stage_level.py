"""Tests explícitos a nivel de etapa (13.1/13.2): la regla literal
"5 solicitadas + 2 elegibles = 2 publicadas" e insufficient_evidence por cada
causa (fuentes <3, dominios críticos <2, criterio crítico null, competencia)."""

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from bore.agents.schemas import CriterionAssessmentOutput, CriterionScore
from bore.db import repositories as repo
from bore.db.tables import opportunities
from bore.domain import scoring
from bore.orchestration import stages
from bore.orchestration.budgets import BudgetManager, load_pricing
from bore.orchestration.stages import PipelineContext
from bore.providers.contracts import AnalysisResult, UsageRecord
from bore.providers.fakes import make_fake_providers

from .conftest import DEMO_CONFIG

pytestmark = pytest.mark.requires_services


def _ctx(s, db, run_id, config=None, analysis=None):
    research, _analysis, verifier = make_fake_providers()
    limits = (config or DEMO_CONFIG)["limits"]
    return PipelineContext(
        session=s,
        session_factory=db,
        run_id=run_id,
        config=config or DEMO_CONFIG,
        is_synthetic=True,
        research=research,
        analysis=analysis or _analysis,
        verifier=verifier,
        budget=BudgetManager(
            max_usd=Decimal(str(limits["max_usd"])),
            max_web_search_calls=limits["max_web_search_calls"],
            max_model_calls=limits["max_model_calls"],
            elapsed_seconds=lambda: 0.0,
            max_duration_seconds=limits["max_duration_seconds"],
            reserve_remaining=0,
            pricing=load_pricing(),
        ),
        pricing=load_pricing(),
    )


FULL = {c: 8 for c in scoring.WEIGHTS}


async def _scored_opp(s, run_id, name, *, n_sources=3, same_domain=False):
    opp = await repo.insert_opportunity(
        s,
        research_run_id=run_id,
        fingerprint=name,
        name=name,
        sector_subcategory="s",
        customer_segment="seg",
        buyer="b",
        problem="p",
        solution="sol",
        value_proposition="v",
        business_model="m",
        status="scored",
    )
    domains = (
        ["informes.example.com"] * n_sources
        if same_domain
        else ["a.example.com", "b.example.org", "c.example.net"][:n_sources]
    )
    for i, domain in enumerate(domains):
        src = await repo.upsert_source(
            s,
            research_run_id=run_id,
            url=f"https://{domain}/doc-{name}-{i}",
            canonical_url=f"https://{domain}/doc-{name}-{i}",
            registrable_domain=domain,
            provenance="synthetic",
            title="t",
            access_status="ok",
        )
        claim_id = uuid.uuid4()
        await repo.insert_claim(
            s,
            claim_id,
            research_run_id=run_id,
            opportunity_id=opp["id"],
            stage="market_validation",
            origin_agent="x",
            criterion_code="demand",
            text=f"Afirmación {i}",
            kind="fact",
            verification_status="verified",
            disputed=False,
        )
        await repo.insert_claim_source(
            s, claim_id=claim_id, source_id=src["id"], support_type="supports"
        )
    return opp


async def test_five_requested_two_qualify_publishes_two(db, run_id):
    """5 solicitadas + exactamente 2 cualifican -> 2 publicadas, sin rellenar."""
    config = dict(DEMO_CONFIG)
    config["requested_ideas"] = 5
    async with db() as s:
        ctx = _ctx(s, db, run_id, config)
        o1 = await _scored_opp(s, run_id, "buena-1")
        o2 = await _scored_opp(s, run_id, "buena-2")
        weak = await _scored_opp(s, run_id, "floja")
        final = {
            str(o1["id"]): dict(FULL),
            str(o2["id"]): dict(FULL),
            str(weak["id"]): {c: 3 for c in scoring.WEIGHTS},
        }
        await s.commit()
        out = await stages.stage_ranking(ctx, final)
        assert out["published"] == 2
        assert out["statistics"]["qualified_not_published"] == 0
        assert out["statistics"]["below_threshold"] == 1
        statuses = {
            r.name: r.status
            for r in (
                await s.execute(
                    sa.select(opportunities).where(opportunities.c.research_run_id == run_id)
                )
            ).all()
        }
        assert statuses["buena-1"] == "published"
        assert statuses["buena-2"] == "published"
        assert statuses["floja"] == "below_threshold"


async def test_insufficient_below_three_distinct_sources(db, run_id):
    async with db() as s:
        ctx = _ctx(s, db, run_id)
        opp = await _scored_opp(s, run_id, "pocas", n_sources=2)
        await s.commit()
        await stages.stage_ranking(ctx, {str(opp["id"]): dict(FULL)})
        row = await repo.get_opportunity(s, opp["id"])
        assert row["status"] == "insufficient_evidence"


async def test_insufficient_below_two_critical_domains(db, run_id):
    async with db() as s:
        ctx = _ctx(s, db, run_id)
        opp = await _scored_opp(s, run_id, "mismo-dominio", n_sources=3, same_domain=True)
        await s.commit()
        await stages.stage_ranking(ctx, {str(opp["id"]): dict(FULL)})
        row = await repo.get_opportunity(s, opp["id"])
        assert row["status"] == "insufficient_evidence"


class _MissingCriterionAnalysis:
    """Evaluador que omite un criterio crítico en su salida."""

    async def analyze(self, task):
        scores = [CriterionScore(criterion=c, score=7) for c in scoring.WEIGHTS if c != "demand"]
        return AnalysisResult(
            output=CriterionAssessmentOutput(scores=scores),
            usage=UsageRecord(provider="fake", model="fake", requests=1),
        )


async def test_insufficient_when_critical_criterion_null(db, run_id):
    """Un criterio crítico sin nota (null por base mínima) -> insufficient_evidence."""
    async with db() as s:
        ctx = _ctx(s, db, run_id, analysis=_MissingCriterionAnalysis())
        opp = await repo.insert_opportunity(
            s,
            research_run_id=run_id,
            fingerprint="x",
            name="sin-criterio",
            sector_subcategory="s",
            customer_segment="seg",
            buyer="b",
            problem="p",
            solution="sol",
            value_proposition="v",
            business_model="m",
            status="shortlisted",
        )
        await s.commit()
        await stages.stage_scoring(ctx)
        row = await repo.get_opportunity(s, opp["id"])
        assert row["status"] == "insufficient_evidence"


async def test_insufficient_competition_unknown(db, run_id):
    """Shortlisted sin competidores ni sustitutos -> competition_unknown."""
    async with db() as s:
        ctx = _ctx(s, db, run_id)
        opp = await repo.insert_opportunity(
            s,
            research_run_id=run_id,
            fingerprint="y",
            name="sin-competencia",
            sector_subcategory="s",
            customer_segment="seg",
            buyer="b",
            problem="p",
            solution="sol",
            value_proposition="v",
            business_model="m",
            status="shortlisted",
        )
        await s.commit()
        await stages.stage_evidence_audit(ctx)
        row = await repo.get_opportunity(s, opp["id"])
        assert row["status"] == "insufficient_evidence"
        assert row["rejection_code"] == "competition_unknown"
