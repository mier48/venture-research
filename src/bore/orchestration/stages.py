"""Implementación de las 10 etapas del pipeline (sección 4).

Cada etapa persiste checkpoints (stage_runs) antes de avanzar y es idempotente:
un reintento no duplica oportunidades, fuentes ni notas.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bore.agents.schemas import (
    CriterionAssessmentOutput,
    ExtractedClaim,
    MarketValidationOutput,
)
from bore.db import repositories as repo
from bore.domain import confidence as confidence_mod
from bore.domain import deduplication, eligibility, evidence, scoring
from bore.domain.enums import (
    AccessStatus,
    ClaimKind,
    ClaimSourceRole,
    EligibilityAnswer,
    JudgeVerdict,
    OpportunityStatus,
    Provenance,
    RejectionCode,
    StageId,
    VerificationStatus,
)
from bore.domain.models import check_transition
from bore.domain.unit_economics import required_paying_customers
from bore.orchestration.budgets import BudgetManager, Pricing
from bore.providers.contracts import (
    AnalysisProvider,
    AnalysisTask,
    ResearchProvider,
    ResearchTask,
    SourceVerifier,
)

MAX_TASKS_PARALLEL = 3


def input_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class PipelineContext:
    session: AsyncSession
    session_factory: Any
    run_id: uuid.UUID
    config: dict[str, Any]
    is_synthetic: bool
    research: ResearchProvider
    analysis: AnalysisProvider
    verifier: SourceVerifier
    budget: BudgetManager
    pricing: Pricing
    provider_name: str = "fake"
    model: str | None = "fake-model"
    stage: StageId | None = None
    idea_index_by_opp: dict[uuid.UUID, int] = field(default_factory=dict)
    pending_price_claims: dict[tuple[uuid.UUID, str], uuid.UUID] = field(default_factory=dict)
    page_texts: dict[str, str] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_TASKS_PARALLEL)
    )

    async def event(
        self, event_type: str, message: str, *, level: str = "info", payload: Any = None
    ) -> None:
        await repo.insert_event(
            self.session,
            self.run_id,
            level=level,
            event_type=event_type,
            stage=self.stage.value if self.stage else None,
            message_safe=message,
            payload=payload,
        )
        await self.session.commit()

    async def check_cancel(self) -> None:
        if await repo.is_cancel_requested(self.session, self.run_id):
            raise PipelineCancelled()

    async def do_research(
        self, role: str, input_text: str, *, searches: int, opp: uuid.UUID | None = None
    ) -> Any:
        from bore.providers.contracts import ResearchResult

        assert self.stage is not None
        self.budget.check_before_call(web_searches=searches)
        self.budget.search_budget_remaining = max(0, self.budget.search_budget_remaining)
        task = ResearchTask(
            role=role,
            stage=self.stage.value,
            instructions=f"Investiga y devuelve fuentes. {input_text}",
            input_text=input_text,
            country=self.config["country"],
            language=self.config["language"],
            opportunity_id=str(opp) if opp else None,
        )
        result: ResearchResult = await self.research.research(task)
        await self._record_call(result.usage, role, opp, "ok")
        return result

    async def do_analysis(self, role: str, input_text: str, *, opp: uuid.UUID | None = None) -> Any:
        assert self.stage is not None
        self.budget.check_before_call(web_searches=0)
        task: AnalysisTask[Any] = AnalysisTask(
            role=role,
            stage=self.stage.value,
            instructions="Estructura la información según el esquema.",
            input_text=input_text,
            opportunity_id=str(opp) if opp else None,
        )
        result = await self.analysis.analyze(task)
        await self._record_call(result.usage, role, opp, "ok")
        return result.output

    async def _record_call(
        self, usage: Any, role: str, opp: uuid.UUID | None, outcome: str
    ) -> None:
        assert self.stage is not None
        cost, estimated = self.pricing.call_cost(usage.model, usage)
        await repo.insert_provider_call(
            self.session,
            research_run_id=self.run_id,
            stage=self.stage.value,
            agent_role=role,
            opportunity_id=opp,
            provider=usage.provider,
            model=usage.model,
            requests=usage.requests,
            tokens_input=usage.tokens_input,
            tokens_cached=usage.tokens_cached_input,
            tokens_output=usage.tokens_output,
            tokens_reasoning=usage.tokens_reasoning,
            web_search_calls=usage.web_search_calls,
            cost_usd=cost,
            cost_estimated=estimated,
            duration_ms=usage.duration_ms,
            outcome=outcome,
        )
        self.budget.record(usage, cost)


class PipelineCancelled(Exception):
    pass


def _sources_block(sources: Any) -> str:
    lines = [f"{s.ref}: {s.url} — {s.title or ''}" for s in sources]
    return "Fuentes disponibles:\n" + "\n".join(lines)


_PROVENANCE_BY_ORIGIN = {
    "annotation": Provenance.PROVIDER_ANNOTATION,
    "source_list": Provenance.PROVIDER_SOURCE_LIST,
    "synthetic": Provenance.SYNTHETIC,
}


async def _persist_sources(ctx: PipelineContext, sources: Any) -> dict[str, uuid.UUID]:
    """Persiste fuentes deduplicadas por canonical_url; ref -> source_id."""
    ref_map: dict[str, uuid.UUID] = {}
    seen: dict[str, uuid.UUID] = {}
    for s in sources:
        canon = evidence.canonical_url(s.url)
        if canon in seen:
            ref_map[s.ref] = seen[canon]
            continue
        row = await repo.upsert_source(
            ctx.session,
            research_run_id=ctx.run_id,
            url=s.url,
            canonical_url=canon,
            registrable_domain=evidence.registrable_domain(s.url),
            title=s.title,
            source_type="web",
            provenance=_PROVENANCE_BY_ORIGIN[s.origin].value,
            access_status=AccessStatus.NOT_CHECKED.value,
            provider_reference=s.ref,
        )
        seen[canon] = row["id"]
        ref_map[s.ref] = row["id"]
    return ref_map


async def persist_claims(
    ctx: PipelineContext,
    claims: list[ExtractedClaim],
    ref_map: dict[str, uuid.UUID],
    *,
    opportunity_id: uuid.UUID | None,
    origin_agent: str,
) -> list[uuid.UUID]:
    """Validación 4.2 en código: refs desconocidas se descartan con evento
    `unknown_source_ref`; un fact sin refs válidas degrada a hypothesis."""
    ids: list[uuid.UUID] = []
    assert ctx.stage is not None
    for c in claims:
        valid_refs = [r for r in c.source_refs if r in ref_map]
        for r in c.source_refs:
            if r not in ref_map:
                await ctx.event(
                    "unknown_source_ref",
                    f"Referencia de fuente desconocida descartada: {r}",
                    level="warning",
                    payload={"ref": r, "role": origin_agent},
                )
        kind = c.kind
        if kind == "fact" and not valid_refs:
            kind = "hypothesis"
        claim_id = uuid.uuid4()
        await repo.insert_claim(
            ctx.session,
            claim_id,
            research_run_id=ctx.run_id,
            opportunity_id=opportunity_id,
            stage=ctx.stage.value,
            origin_agent=origin_agent,
            criterion_code=c.criterion,
            text=c.text,
            kind=kind,
            verification_status=VerificationStatus.UNVERIFIED.value,
            disputed=False,
        )
        for r in valid_refs:
            await repo.insert_claim_source(
                ctx.session,
                claim_id,
                ref_map[r],
                support_type="supports",
                evidence_quote=c.evidence_quote,
            )
        ids.append(claim_id)
    return ids


async def audit_claims(ctx: PipelineContext, claim_ids: list[uuid.UUID]) -> None:
    """Auditoría 4.4: verifica fuentes, quote_match, juez y estado calculado."""
    for source in await repo.list_sources(ctx.session, ctx.run_id):
        if source["access_status"] != AccessStatus.NOT_CHECKED.value:
            continue
        check = await ctx.verifier.verify(source["url"])
        await repo.update_source(
            ctx.session,
            source["id"],
            final_url=check.final_url,
            access_status=check.access_status.value,
            http_status=check.http_status,
            content_hash=check.content_hash,
            excerpt=(check.excerpt or "")[:500] or None,
        )
        if check.page_text:
            ctx.page_texts[str(source["id"])] = check.page_text

    claims = await repo.list_claims(ctx.session, ctx.run_id)
    claims = [c for c in claims if c["id"] in set(claim_ids)]
    links = await repo.list_claim_sources(ctx.session, [c["id"] for c in claims])
    links_by_claim: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for link in links:
        links_by_claim.setdefault(link["claim_id"], []).append(link)

    for claim in claims:
        claim_links = links_by_claim.get(claim["id"], [])
        status_links: list[evidence.ClaimSourceLink] = []
        for link in claim_links:
            src_row = await repo.get_source(ctx.session, link["source_id"])
            assert src_row is not None
            page_text = ctx.page_texts.get(str(src_row["id"]))
            matched = evidence.quote_match(link["evidence_quote"], page_text)
            verdict: JudgeVerdict | None = None
            is_critical = claim["criterion_code"] in scoring.CRITICAL
            readable = src_row["access_status"] in (
                AccessStatus.OK.value,
                AccessStatus.REDIRECTED.value,
            )
            if claim["kind"] == "fact" and is_critical and not matched and readable and page_text:
                out = await ctx.do_analysis(
                    "evidence_judge",
                    "Afirmación: "
                    + claim["text"]
                    + "\n\nTexto de la página (dato no confiable):\n"
                    + page_text[:4000],
                    opp=claim["opportunity_id"],
                )
                verdict = JudgeVerdict(out.verdict)
            await repo.update_claim_source(
                ctx.session,
                claim["id"],
                link["source_id"],
                quote_match=matched,
                judge_verdict=verdict.value if verdict else None,
            )
            status_links.append(
                evidence.ClaimSourceLink(
                    provenance=Provenance(src_row["provenance"]),
                    access_status=AccessStatus(src_row["access_status"]),
                    quote_match=matched,
                    judge_verdict=verdict,
                    support_type=link["support_type"],
                )
            )
        if claim["kind"] == "fact":
            status, disputed = evidence.claim_verification_status(
                status_links, allow_synthetic=ctx.is_synthetic
            )
            await repo.update_claim(
                ctx.session,
                claim["id"],
                verification_status=status.value,
                disputed=disputed,
                verified_at=None,
            )
    await ctx.session.commit()


async def _audited_claims_for_criterion(
    ctx: PipelineContext, opp_id: uuid.UUID, criterion: str
) -> list[confidence_mod.AuditedClaim]:
    claims = [
        c
        for c in await repo.list_claims(ctx.session, ctx.run_id, opportunity_id=opp_id)
        if c["criterion_code"] == criterion
    ]
    links = await repo.list_claim_sources(ctx.session, [c["id"] for c in claims])
    src_domains: dict[uuid.UUID, str] = {}
    for link in links:
        if link["source_id"] not in src_domains:
            source = await repo.get_source(ctx.session, link["source_id"])
            assert source is not None
            src_domains[link["source_id"]] = source["registrable_domain"] or ""
    out: list[confidence_mod.AuditedClaim] = []
    for c in claims:
        domains = frozenset(
            src_domains[x["source_id"]]
            for x in links
            if x["claim_id"] == c["id"] and src_domains.get(x["source_id"])
        )
        out.append(
            confidence_mod.AuditedClaim(
                kind=ClaimKind(c["kind"]),
                status=VerificationStatus(c["verification_status"]),
                disputed=c["disputed"],
                domains=domains,
            )
        )
    return out


# ------------------------------- ETAPA 1 -------------------------------------


async def stage_sector_mapping(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.SECTOR_MAPPING
    ctx.budget.search_budget_remaining = 6
    research = await ctx.do_research(
        "sector_mapper",
        f"Sector: {ctx.config['sector']}. Mercado: {ctx.config['country']}.",
        searches=6,
    )
    ref_map = await _persist_sources(ctx, research.sources)
    output = await ctx.do_analysis(
        "sector_mapper", research.text + "\n\n" + _sources_block(research.sources)
    )
    claim_ids = await persist_claims(
        ctx, output.claims, ref_map, opportunity_id=None, origin_agent="sector_mapper"
    )
    result: dict[str, Any] = output.model_dump()
    result["claim_ids"] = [str(i) for i in claim_ids]
    return result


# ------------------------------- ETAPA 2 -------------------------------------


async def stage_demand_research(ctx: PipelineContext, prev: dict[str, Any]) -> dict[str, Any]:
    ctx.stage = StageId.DEMAND_RESEARCH
    ctx.budget.search_budget_remaining = 16
    research = await ctx.do_research(
        "demand_research",
        "Problemas y segmentos: " + json.dumps(prev.get("problems", [])),
        searches=16,
    )
    ref_map = await _persist_sources(ctx, research.sources)
    output = await ctx.do_analysis(
        "demand_researcher", research.text + "\n\n" + _sources_block(research.sources)
    )
    claim_ids = await persist_claims(
        ctx, output.claims, ref_map, opportunity_id=None, origin_agent="demand_researcher"
    )
    result: dict[str, Any] = output.model_dump()
    result["claim_ids"] = [str(i) for i in claim_ids]
    return result


# ------------------------------- ETAPA 3 -------------------------------------


async def stage_candidate_generation(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.CANDIDATE_GENERATION
    output = await ctx.do_analysis(
        "idea_generator",
        f"Genera hasta {ctx.config['candidate_pool_size']} candidatas; máximo "
        f"{ctx.config['max_candidates_per_subsector']} por subsector.",
    )
    opp_ids: list[uuid.UUID] = []
    for idx, idea in enumerate(output.ideas):
        fp = deduplication.fingerprint(idea.customer_segment, idea.problem)
        row = await repo.insert_opportunity(
            ctx.session,
            research_run_id=ctx.run_id,
            fingerprint=fp,
            name=idea.name,
            sector_subcategory=idea.sector_subcategory,
            customer_segment=idea.customer_segment,
            buyer=idea.buyer,
            problem=idea.problem,
            solution=idea.solution,
            value_proposition=idea.value_proposition,
            business_model=idea.business_model,
            mvp_scope=idea.mvp_scope,
            excluded_features=idea.excluded_features,
            acquisition={"channels": idea.acquisition_channels},
            assumptions={"items": idea.assumptions},
            status=OpportunityStatus.CANDIDATE.value,
        )
        if row is not None:
            ctx.idea_index_by_opp[row["id"]] = idx
            opp_ids.append(row["id"])
    await ctx.session.commit()

    dedup = await ctx.do_analysis(
        "dedup_screener",
        f"Agrupa candidatas equivalentes entre las {len(output.ideas)} recibidas.",
    )
    # Los grupos del modelo refieren índices de la lista de ideas; el código
    # conserva la más completa y marca el resto como duplicate.
    kept: set[int] = set()
    for group in dedup.groups:
        members = [i for i in group if i < len(output.ideas)]
        if len(members) < 2:
            continue
        keeper = min(members)
        kept.add(keeper)
        for dup_idx in members:
            if dup_idx == keeper:
                continue
            dup_opp = next((oid for oid, i in ctx.idea_index_by_opp.items() if i == dup_idx), None)
            keep_opp = next((oid for oid, i in ctx.idea_index_by_opp.items() if i == keeper), None)
            if dup_opp and keep_opp:
                check_transition(OpportunityStatus.CANDIDATE, OpportunityStatus.DUPLICATE)
                await repo.update_opportunity(
                    ctx.session,
                    dup_opp,
                    status=OpportunityStatus.DUPLICATE.value,
                    duplicate_of_id=keep_opp,
                )
                del ctx.idea_index_by_opp[dup_opp]
    await ctx.session.commit()
    return {"generated": len(output.ideas), "opportunity_ids": [str(i) for i in opp_ids]}


# ------------------------------- ETAPA 4 -------------------------------------


async def stage_eligibility(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.ELIGIBILITY_FILTER

    async def assess(opp_id: uuid.UUID) -> None:
        async with ctx.semaphore, ctx.session_factory() as task_session:
            tctx = dataclasses.replace(ctx, session=task_session)
            await tctx.check_cancel()
            idx = ctx.idea_index_by_opp[opp_id]
            out = await tctx.do_analysis(
                "eligibility_assessor", f"[idea_index={idx}] Evalúa.", opp=opp_id
            )
            assessments = [
                eligibility.RuleAssessment(
                    rule=RejectionCode(a.rule),
                    answer=EligibilityAnswer(a.answer),
                    reason=a.reason,
                )
                for a in out.assessments
            ]
            decision = eligibility.decide(assessments)
            current = OpportunityStatus.CANDIDATE
            check_transition(current, decision.status)
            await repo.update_opportunity(
                task_session,
                opp_id,
                status=decision.status.value,
                rejection_code=decision.rejection_code.value if decision.rejection_code else None,
                eligibility={
                    "assessments": [
                        {"rule": a.rule, "answer": a.answer, "reason": a.reason}
                        for a in out.assessments
                    ],
                    "uncertainties": [u.value for u in decision.uncertainties],
                    "reason": decision.reason,
                },
            )
            await task_session.commit()

    await asyncio.gather(*[assess(o) for o in ctx.idea_index_by_opp])
    await ctx.session.commit()
    return {"evaluated": len(ctx.idea_index_by_opp)}


# ------------------------------- ETAPA 5 -------------------------------------


async def stage_screening(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.SHALLOW_SCREENING
    eligible = [
        o for o in await repo.list_opportunities(ctx.session, ctx.run_id, status="eligible")
    ]
    limit = ctx.config["deep_dive_limit"]
    per_opp_budget = min(2 * len(eligible), 30)

    async def probe(opp: dict[str, Any]) -> None:
        async with ctx.semaphore, ctx.session_factory() as task_session:
            tctx = dataclasses.replace(ctx, session=task_session)
            await tctx.check_cancel()
            idx = ctx.idea_index_by_opp[opp["id"]]
            research = await tctx.do_research(
                "screener", f"[opp={idx}] Sondeo de {opp['name']}.", searches=2, opp=opp["id"]
            )
            out = await tctx.do_analysis(
                "screener",
                f"[opp={idx}] {research.text}",
                opp=opp["id"],
            )
            score = scoring.screening_score(
                {
                    "demand": out.demand,
                    "distribution": out.distribution,
                    "competition": out.competition,
                }
            )
            await repo.update_opportunity(ctx.session, opp["id"], screening_score=score)
            await repo.insert_event(
                ctx.session,
                ctx.run_id,
                level="info",
                event_type="screening_probe",
                stage=ctx.stage.value if ctx.stage else None,
                message_safe=f"Sondeo de {opp['name']}",
                payload={"sources": len(research.sources)},
            )
            await task_session.commit()

    ctx.budget.search_budget_remaining = per_opp_budget
    await asyncio.gather(*[probe(o) for o in eligible])
    await ctx.session.commit()

    eligible = await repo.list_opportunities(ctx.session, ctx.run_id, status="eligible")
    ranked = sorted(
        eligible,
        key=lambda o: (o["screening_score"] or Decimal(0), o["generated_at"]),
        reverse=True,
    )
    shortlisted = ranked[:limit]
    for opp in shortlisted:
        check_transition(OpportunityStatus.ELIGIBLE, OpportunityStatus.SHORTLISTED)
        await repo.update_opportunity(
            ctx.session, opp["id"], status=OpportunityStatus.SHORTLISTED.value
        )
    for opp in ranked[limit:]:
        check_transition(OpportunityStatus.ELIGIBLE, OpportunityStatus.NOT_SHORTLISTED)
        await repo.update_opportunity(
            ctx.session, opp["id"], status=OpportunityStatus.NOT_SHORTLISTED.value
        )
    await ctx.session.commit()
    return {"shortlisted": [str(o["id"]) for o in shortlisted]}


# ------------------------------- ETAPA 6 -------------------------------------


async def stage_market_validation(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.MARKET_VALIDATION
    shortlisted = await repo.list_opportunities(ctx.session, ctx.run_id, status="shortlisted")
    ctx.budget.search_budget_remaining = 10 * len(shortlisted)

    async def validate(opp: dict[str, Any]) -> None:
        async with ctx.semaphore, ctx.session_factory() as task_session:
            tctx = dataclasses.replace(ctx, session=task_session)
            await tctx.check_cancel()
            idx = ctx.idea_index_by_opp[opp["id"]]
            research = await tctx.do_research(
                "market_validation",
                f"[opp={idx}] Validación de mercado de {opp['name']}.",
                searches=10,
                opp=opp["id"],
            )
            ref_map = await _persist_sources(tctx, research.sources)
            out: MarketValidationOutput = await tctx.do_analysis(
                "market_validation",
                f"[opp={idx}] {research.text}\n\n{_sources_block(research.sources)}",
                opp=opp["id"],
            )
            claim_ids = await persist_claims(
                tctx,
                out.claims,
                ref_map,
                opportunity_id=opp["id"],
                origin_agent="market_validation",
            )
            for comp in out.competitors:
                price_claim_id = (
                    claim_ids[comp.price_claim_index]
                    if comp.price_claim_index is not None
                    and comp.price_claim_index < len(claim_ids)
                    else None
                )
                await repo.insert_competitor(
                    task_session,
                    research_run_id=ctx.run_id,
                    opportunity_id=opp["id"],
                    name=comp.name,
                    website_url=comp.website_url,
                    category=comp.category,
                    positioning=comp.positioning,
                    price_value=comp.price_value,
                    price_currency=comp.price_currency,
                    price_unit=comp.price_unit,
                    price_country=comp.price_country,
                    price_status="unverified",
                    price_claim_id=price_claim_id,
                    strengths=comp.strengths,
                    weaknesses=comp.weaknesses,
                )
            price = Decimal(str(out.price_hypothesis_eur_month or 0))
            var = Decimal(str(out.variable_cost_eur_month or 0))
            fixed = Decimal(str(out.fixed_cost_eur_month or 0))
            target = Decimal(str(ctx.config["min_target_operating_profit_eur_month"]))
            customers = required_paying_customers(target, price, var, fixed) if price > 0 else None
            await task_session.commit()
            await repo.update_opportunity(
                task_session,
                opp["id"],
                unit_economics={
                    "hypothesis": True,
                    "price_eur_month": str(price),
                    "variable_cost_eur_month": str(var),
                    "fixed_cost_eur_month": str(fixed),
                    "required_paying_customers": customers,
                },
                risks={"technical_notes": out.technical_notes},
            )
            await task_session.commit()

    await asyncio.gather(*[validate(o) for o in shortlisted])
    await ctx.session.commit()
    return {"validated": len(shortlisted)}


# ------------------------------- ETAPA 7 -------------------------------------


async def stage_evidence_audit(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.EVIDENCE_AUDIT
    all_claims = await repo.list_claims(ctx.session, ctx.run_id)
    await audit_claims(ctx, [c["id"] for c in all_claims])
    all_claims = await repo.list_claims(ctx.session, ctx.run_id)

    # Precio verificado solo si la afirmación de precio quedó `verified`.
    shortlisted = await repo.list_opportunities(ctx.session, ctx.run_id, status="shortlisted")
    claims_by_id = {c["id"]: c for c in all_claims}
    for opp in shortlisted:
        comps = await repo.list_competitors(ctx.session, opp["id"])
        for comp in comps:
            status = "not_public" if comp["price_value"] is None else "unverified"
            if comp["price_claim_id"]:
                claim = claims_by_id.get(comp["price_claim_id"])
                if claim and claim["verification_status"] == VerificationStatus.VERIFIED.value:
                    status = "verified"
            await repo.update_competitor(ctx.session, comp["id"], price_status=status)
        # competition_unknown -> insufficient_evidence (nunca «sin competencia»).
        if not comps:
            check_transition(OpportunityStatus.SHORTLISTED, OpportunityStatus.INSUFFICIENT_EVIDENCE)
            await repo.update_opportunity(
                ctx.session,
                opp["id"],
                status=OpportunityStatus.INSUFFICIENT_EVIDENCE.value,
                rejection_code="competition_unknown",
            )
            await ctx.event(
                "competition_unknown",
                f"No se localizaron competidores ni sustitutos para {opp['name']}",
                level="warning",
            )
    await ctx.session.commit()
    return {"audited_claims": len(all_claims)}


# ------------------------------- ETAPA 8 -------------------------------------


async def _score_once(
    ctx: PipelineContext, opp_id: uuid.UUID, revision: str
) -> dict[str, int] | None:
    idx = ctx.idea_index_by_opp.get(opp_id)
    marker = f"[opp={idx}][revision={revision}]"
    out: CriterionAssessmentOutput = await ctx.do_analysis("criterion_assessor", marker, opp=opp_id)
    scores: dict[str, int] = {}
    per_conf: dict[str, Any] = {}
    for item in out.scores:
        audited = await _audited_claims_for_criterion(ctx, opp_id, item.criterion)
        is_critical = item.criterion in scoring.CRITICAL
        base_ok = confidence_mod.has_min_evidence_base(audited, is_critical=is_critical)
        conf = confidence_mod.criterion_confidence(audited)
        per_conf[item.criterion] = conf
        score = item.score if base_ok else None
        row = await repo.insert_criterion_score(
            ctx.session,
            opportunity_id=opp_id,
            rubric_version=scoring.RUBRIC_VERSION,
            criterion_code=item.criterion,
            score=score,
            confidence=conf.value,
            rationale=item.rationale,
            unknowns=item.unknowns,
            revision=revision,
        )
        if revision == "final" and score is not None and item.score != score:
            await repo.update_criterion_score(ctx.session, row["id"], score=score)
        if score is None:
            continue
        scores[item.criterion] = score
        claims = await repo.list_claims(ctx.session, ctx.run_id, opportunity_id=opp_id)
        for c in claims:
            if c["criterion_code"] == item.criterion:
                role = (
                    ClaimSourceRole.COUNTEREVIDENCE.value
                    if c["verification_status"] == VerificationStatus.CONTRADICTED.value
                    or c["disputed"]
                    else ClaimSourceRole.EVIDENCE.value
                )
                await repo.insert_score_claim(ctx.session, row["id"], c["id"], role)
    if set(scores) != set(scoring.WEIGHTS):
        return None
    from bore.domain.enums import Confidence

    safe_conf = {c: per_conf.get(c, Confidence.LOW) for c in scoring.WEIGHTS}
    opp_conf = confidence_mod.global_confidence(safe_conf)
    await repo.update_opportunity(ctx.session, opp_id, confidence=opp_conf.value)
    return scores


async def stage_scoring(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.SCORING
    shortlisted = await repo.list_opportunities(ctx.session, ctx.run_id, status="shortlisted")
    initial: dict[str, dict[str, int]] = {}
    for opp in shortlisted:
        await ctx.check_cancel()
        scores = await _score_once(ctx, opp["id"], "initial")
        if scores is None:
            check_transition(OpportunityStatus.SHORTLISTED, OpportunityStatus.INSUFFICIENT_EVIDENCE)
            await repo.update_opportunity(
                ctx.session, opp["id"], status=OpportunityStatus.INSUFFICIENT_EVIDENCE.value
            )
        else:
            initial[str(opp["id"])] = scores
            check_transition(OpportunityStatus.SHORTLISTED, OpportunityStatus.SCORED)
            await repo.update_opportunity(
                ctx.session, opp["id"], status=OpportunityStatus.SCORED.value
            )
    await ctx.session.commit()
    return {"scored": initial}


# ------------------------------- ETAPA 9 -------------------------------------


async def stage_adversarial_review(
    ctx: PipelineContext, initial_scores: dict[str, dict[str, int]]
) -> dict[str, Any]:
    ctx.stage = StageId.ADVERSARIAL_REVIEW
    scored = await repo.list_opportunities(ctx.session, ctx.run_id, status="scored")
    ordered = sorted(
        scored,
        key=lambda o: scoring.weighted_score(initial_scores[str(o["id"])]),
        reverse=True,
    )
    to_review = ordered[: ctx.config["review_limit"]]
    rest = ordered[ctx.config["review_limit"] :]
    ctx.budget.search_budget_remaining = 4 * len(to_review)

    final_scores: dict[str, dict[str, int]] = {}
    for opp in to_review:
        await ctx.check_cancel()
        idx = ctx.idea_index_by_opp.get(opp["id"])
        research = await ctx.do_research(
            "critical_reviewer",
            f"[opp={idx}] Busca contraevidencia de {opp['name']}.",
            searches=4,
            opp=opp["id"],
        )
        ref_map = await _persist_sources(ctx, research.sources)
        review = await ctx.do_analysis(
            "critical_reviewer",
            f"[opp={idx}] {research.text}\n\n{_sources_block(research.sources)}",
            opp=opp["id"],
        )
        counter_ids = await persist_claims(
            ctx,
            review.counter_claims,
            ref_map,
            opportunity_id=opp["id"],
            origin_agent="critical_reviewer",
        )
        await repo.insert_review(
            ctx.session,
            opportunity_id=opp["id"],
            reviewer_role="critical_reviewer",
            objections=review.objections,
            recommended_changes=review.recommended_changes,
            final_verdict=review.final_verdict,
        )
        # Reauditoría de las nuevas afirmaciones.
        await audit_claims(ctx, counter_ids)
        final = await _score_once(ctx, opp["id"], "final")
        if final is not None:
            final_scores[str(opp["id"])] = final
            await repo.update_opportunity(
                ctx.session,
                opp["id"],
                final_score=scoring.weighted_score(final),
            )
    for opp in rest:
        check_transition(OpportunityStatus.SCORED, OpportunityStatus.NOT_REVIEWED)
        await repo.update_opportunity(
            ctx.session, opp["id"], status=OpportunityStatus.NOT_REVIEWED.value
        )
    await ctx.session.commit()
    return {"reviewed": [str(o["id"]) for o in to_review], "final_scores": final_scores}


# ------------------------------- ETAPA 10 ------------------------------------


async def stage_ranking(
    ctx: PipelineContext, final_scores: dict[str, dict[str, int]]
) -> dict[str, Any]:
    ctx.stage = StageId.RANKING_AND_REPORT
    min_final = Decimal(str(ctx.config["min_final_score"]))
    qualifying: list[tuple[dict[str, Any], Decimal]] = []

    scored = await repo.list_opportunities(ctx.session, ctx.run_id, status="scored")
    for opp in scored:
        scores = final_scores.get(str(opp["id"]))
        if scores is None:
            continue
        claims = await repo.list_claims(ctx.session, ctx.run_id, opportunity_id=opp["id"])
        links = await repo.list_claim_sources(ctx.session, [c["id"] for c in claims])
        source_ids = {x["source_id"] for x in links}
        critical_domains: set[str] = set()
        contradicted_central = False
        for c in claims:
            if c["verification_status"] == VerificationStatus.CONTRADICTED.value and (
                c["criterion_code"] in scoring.CRITICAL
            ):
                contradicted_central = True
        for sid in source_ids:
            source = await repo.get_source(ctx.session, sid)
            if source and source["registrable_domain"]:
                critical_domains.add(source["registrable_domain"])
        gates_ok = scoring.passes_gates(
            scores,
            min_final=min_final,
            min_critical=ctx.config["min_critical_criterion_score"],
            min_any=ctx.config["min_any_criterion_score"],
        )
        enough_sources = len(source_ids) >= 3 and len(critical_domains) >= 2
        if contradicted_central or not enough_sources:
            check_transition(OpportunityStatus.SCORED, OpportunityStatus.INSUFFICIENT_EVIDENCE)
            await repo.update_opportunity(
                ctx.session, opp["id"], status=OpportunityStatus.INSUFFICIENT_EVIDENCE.value
            )
            continue
        if not gates_ok:
            check_transition(OpportunityStatus.SCORED, OpportunityStatus.BELOW_THRESHOLD)
            await repo.update_opportunity(
                ctx.session, opp["id"], status=OpportunityStatus.BELOW_THRESHOLD.value
            )
            continue
        qualifying.append((opp, scoring.weighted_score(scores)))

    confidence_rank = {"high": 2, "medium": 1, "low": 0, None: -1}
    qualifying.sort(
        key=lambda pair: (
            pair[1],
            confidence_rank.get(pair[0]["confidence"], -1),
            final_scores[str(pair[0]["id"])]["demand"],
        ),
        reverse=True,
    )
    published = 0
    for rank, (opp, score) in enumerate(qualifying, start=1):
        if published >= ctx.config["requested_ideas"]:
            break
        check_transition(OpportunityStatus.SCORED, OpportunityStatus.PUBLISHED)
        await repo.update_opportunity(
            ctx.session,
            opp["id"],
            status=OpportunityStatus.PUBLISHED.value,
            final_score=score,
            rank=rank,
        )
        published += 1
    await ctx.session.commit()
    stats = await repo.opportunity_status_counts(ctx.session, ctx.run_id)
    return {"published": published, "statistics": stats}
