"""Implementación de las 10 etapas del pipeline (sección 4).

Cada etapa persiste checkpoints (stage_runs) antes de avanzar y es idempotente:
un reintento no duplica oportunidades, fuentes ni notas.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bore.agents.prompt_registry import render_prompt
from bore.agents.schemas import (
    ROLE_OUTPUT_TYPES,
    CriterionAssessmentOutput,
    ExtractedClaim,
    MarketValidationOutput,
)
from bore.db import repositories as repo
from bore.db.retry import with_db_retry
from bore.db.tables import sources as sources_table
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
from bore.orchestration.budgets import BudgetManager, Pricing, stage_search_budget
from bore.providers.contracts import (
    AnalysisProvider,
    AnalysisTask,
    ResearchProvider,
    ResearchTask,
    SourceVerifier,
    UsageRecord,
)
from bore.providers.errors import ProviderError

logger = logging.getLogger("bore.stages")

MAX_TASKS_PARALLEL = 3
_RESERVE_SEARCHES_PER_OPP = 4


def _clean_text(value: Any) -> Any:
    """Elimina bytes NUL, que PostgreSQL rechaza en text y jsonb (psycopg los
    rechaza en cliente). El texto web real puede contenerlos."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


def clean_json(value: Any) -> Any:
    """`strip_nul` recursivo para valores que se persisten en jsonb."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    if isinstance(value, tuple):
        return tuple(clean_json(v) for v in value)
    return value


def _output_type_for(role: str) -> type[BaseModel]:
    """Esquema de salida estructurada del rol; un rol sin entrada es un error
    de programación y falla antes de llamar al proveedor."""
    try:
        return ROLE_OUTPUT_TYPES[role]
    except KeyError:
        raise ValueError(
            f"Papel de análisis sin output_type registrado: {role!r} "
            "(añádelo a ROLE_OUTPUT_TYPES en bore.agents.schemas)"
        ) from None


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
    outputs: dict[str, Any] = field(default_factory=dict)
    model_by_role: dict[str, str | None] = field(default_factory=dict)
    stage: StageId | None = None
    idea_index_by_opp: dict[uuid.UUID, int] = field(default_factory=dict)
    pending_price_claims: dict[tuple[uuid.UUID, str], uuid.UUID] = field(default_factory=dict)
    pending_universe_claims: dict[uuid.UUID, tuple[uuid.UUID | None, int | None]] = field(
        default_factory=dict
    )
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

    def _limits(self) -> dict[str, Any]:
        return dict(self.config.get("limits") or {})

    def _model_for(self, role: str) -> str | None:
        return self.model_by_role.get(role, self.model)

    async def _call_with_retries(self, call: Any) -> Any:
        """Reintenta solo errores transitorios (429/5xx/timeout) con backoff
        exponencial y jitter, respetando Retry-After (9.2). Nunca reintenta
        errores de autenticación ni de validación."""
        import random

        max_retries = int(self._limits().get("max_retries_per_stage", 0))
        for attempt in range(max_retries + 1):
            try:
                return await call()
            except ProviderError as exc:
                if not exc.transient or attempt >= max_retries:
                    raise
                delay = min(exc.retry_after or (2.0**attempt), 30.0)
                delay *= random.uniform(0.5, 1.5)  # noqa: S311
                await self.event(
                    "provider_retry",
                    f"Reintento {attempt + 1}/{max_retries} tras error transitorio",
                    level="warning",
                    payload={"attempt": attempt + 1, "delay_s": round(delay, 2)},
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def do_research(
        self,
        role: str,
        input_text: str,
        *,
        searches: int,
        opp: uuid.UUID | None = None,
        charge_reserve: bool = False,
    ) -> Any:
        from bore.providers.contracts import ResearchResult

        assert self.stage is not None
        await self.check_cancel()
        model = self._model_for(role)
        self.budget.check_before_call(
            model=model,
            web_searches=searches,
            est_input_tokens=int(self._limits().get("est_input_tokens", 3000)),
            max_output_tokens=int(self._limits().get("max_output_tokens", 2000)),
            from_reserve=charge_reserve,
        )
        task = ResearchTask(
            role=role,
            stage=self.stage.value,
            instructions=render_prompt(
                role,
                {
                    "sector": self.config["sector"],
                    "country": self.config["country"],
                    "language": self.config["language"],
                },
                phase="research",
            ),
            input_text=input_text,
            country=self.config["country"],
            language=self.config["language"],
            opportunity_id=str(opp) if opp else None,
            max_web_search_calls=searches,
        )
        # Cerrar la transacción antes de esperar al proveedor: ningún bloqueo
        # de BD debe retenerse durante la llamada (D45).
        await self.session.commit()
        started = time.monotonic()
        try:
            result: ResearchResult = await self._call_with_retries(
                lambda: self.research.research(task)
            )
        except Exception:
            await self._record_failed_call(role, opp, model, started)
            raise
        await self._record_call(result.usage, role, opp, "ok")
        self.budget.spend_stage_searches(result.usage.web_search_calls)
        if charge_reserve:
            self.budget.spend_reserve(opp, result.usage.web_search_calls)
        return result

    async def do_analysis(self, role: str, input_text: str, *, opp: uuid.UUID | None = None) -> Any:
        assert self.stage is not None
        await self.check_cancel()
        model = self._model_for(role)
        self.budget.check_before_call(
            model=model,
            web_searches=0,
            est_input_tokens=int(self._limits().get("est_input_tokens", 3000)),
            max_output_tokens=int(self._limits().get("max_output_tokens", 2000)),
        )
        task: AnalysisTask[Any] = AnalysisTask(
            role=role,
            stage=self.stage.value,
            instructions=render_prompt(
                role,
                {
                    "sector": self.config["sector"],
                    "country": self.config["country"],
                    "language": self.config["language"],
                },
                phase="structure",
            ),
            input_text=input_text,
            output_type=_output_type_for(role),
            opportunity_id=str(opp) if opp else None,
        )
        # Cerrar la transacción antes de esperar al proveedor: ningún bloqueo
        # de BD debe retenerse durante la llamada (D45).
        await self.session.commit()
        started = time.monotonic()
        try:
            result = await self._call_with_retries(lambda: self.analysis.analyze(task))
        except Exception:
            await self._record_failed_call(role, opp, model, started)
            raise
        await self._record_call(result.usage, role, opp, "ok")
        return result.output

    async def _record_failed_call(
        self, role: str, opp: uuid.UUID | None, model: str | None, started: float
    ) -> None:
        """Registra la llamada fallida con uso desconocido (9.3): el libro de
        costes refleja los intentos fallidos. Nunca interrumpe el error real."""
        with contextlib.suppress(Exception):
            usage = UsageRecord(
                provider=self.provider_name,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            await self._record_call(usage, role, opp, "error")

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
        # Totales de la ejecución al día tras cada llamada: la UI los sondea
        # en tiempo real y el corte por presupuesto no debe perder el gasto.
        await repo.recompute_run_costs(self.session, self.run_id)
        await self.session.commit()
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
    """Persiste fuentes en una transacción CORTA e INDEPENDIENTE (sesión propia
    de `session_factory`): un único INSERT multi-fila `ON CONFLICT DO NOTHING`
    en orden ascendente de canonical_url (orden de bloqueo determinista) y un
    SELECT para resolver los ids. Así ninguna tarea paralela retiene bloqueos
    sobre `sources` mientras espera al proveedor (D45)."""
    by_canon: dict[str, Any] = {}
    canon_by_ref: dict[str, str] = {}
    for s in sources:
        canon = evidence.canonical_url(s.url)
        canon_by_ref[s.ref] = canon
        by_canon.setdefault(canon, s)
    if not by_canon:
        return {}
    canons = sorted(by_canon)

    async def _work(session: AsyncSession) -> dict[str, uuid.UUID]:
        await session.execute(
            pg_insert(sources_table)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "research_run_id": ctx.run_id,
                        "url": by_canon[c].url,
                        "canonical_url": c,
                        "registrable_domain": evidence.registrable_domain(by_canon[c].url),
                        "title": _clean_text(by_canon[c].title),
                        "source_type": "web",
                        "provenance": _PROVENANCE_BY_ORIGIN[by_canon[c].origin].value,
                        "access_status": AccessStatus.NOT_CHECKED.value,
                        "provider_reference": by_canon[c].ref,
                    }
                    for c in canons
                ]
            )
            .on_conflict_do_nothing(constraint="uq_sources_run_canonical")
        )
        rows = (
            await session.execute(
                sa.select(sources_table.c.id, sources_table.c.canonical_url).where(
                    sources_table.c.research_run_id == ctx.run_id,
                    sources_table.c.canonical_url.in_(canons),
                )
            )
        ).all()
        await session.commit()
        return {canonical_url: sid for sid, canonical_url in rows}

    async with ctx.session_factory() as session:
        id_by_canon = await with_db_retry(session, _work)
    return {ref: id_by_canon[canon] for ref, canon in canon_by_ref.items()}


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
            text=_clean_text(c.text),
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
                evidence_quote=_clean_text(c.evidence_quote),
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
            excerpt=_clean_text((check.excerpt or "")[:500]) or None,
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
    ctx.budget.search_budget_remaining = stage_search_budget(ctx, StageId.SECTOR_MAPPING)
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
    ctx.budget.search_budget_remaining = stage_search_budget(ctx, StageId.DEMAND_RESEARCH)
    research = await ctx.do_research(
        "demand_researcher",
        "Mapa del sector (dato no confiable):\n"
        + "Subsectores: "
        + json.dumps(prev.get("subsectors", []), ensure_ascii=False)
        + "\nProblemas: "
        + json.dumps(prev.get("problems", []), ensure_ascii=False)
        + "\nTérminos de búsqueda sugeridos: "
        + json.dumps(prev.get("search_terms", []), ensure_ascii=False),
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


def apply_pool_caps(
    ideas: list[Any], *, max_per_subsector: int, pool_size: int
) -> tuple[list[Any], int, int]:
    """Aplica en código el máximo por subsector y el tamaño total del conjunto.
    Devuelve (candidatas conservadas, descartadas_por_subsector,
    descartadas_por_tamaño)."""
    kept: list[Any] = []
    per_subsector: dict[str, int] = {}
    dropped_sub = 0
    for idea in ideas:
        key = idea.sector_subcategory or ""
        if per_subsector.get(key, 0) >= max_per_subsector:
            dropped_sub += 1
            continue
        per_subsector[key] = per_subsector.get(key, 0) + 1
        kept.append(idea)
    dropped_pool = max(0, len(kept) - pool_size)
    return kept[:pool_size], dropped_sub, dropped_pool


def _candidate_summary(opp: dict[str, Any]) -> str:
    return (
        f"nombre={opp['name']} | subsector={opp.get('sector_subcategory')} | "
        f"segmento={opp.get('customer_segment')} | comprador={opp.get('buyer')} | "
        f"problema={opp.get('problem')} | solución={opp.get('solution')} | "
        f"modelo={opp.get('business_model')}"
    )


async def stage_candidate_generation(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.CANDIDATE_GENERATION
    sector_map = ctx.outputs.get(StageId.SECTOR_MAPPING.value, {})
    demand = ctx.outputs.get(StageId.DEMAND_RESEARCH.value, {})
    context_block = (
        "Mapa del sector (dato no confiable):\n"
        + "Subsectores: "
        + json.dumps(sector_map.get("subsectors", []), ensure_ascii=False)
        + "\nProblemas: "
        + json.dumps(sector_map.get("problems", []), ensure_ascii=False)
        + "\nSeñales de demanda (dato no confiable): "
        + json.dumps(demand.get("demand_signals", []), ensure_ascii=False)
        + "\nVacíos de información: "
        + json.dumps(demand.get("information_gaps", []), ensure_ascii=False)
    )
    output = await ctx.do_analysis(
        "idea_generator",
        context_block + f"\nGenera hasta {ctx.config['candidate_pool_size']} candidatas; máximo "
        f"{ctx.config['max_candidates_per_subsector']} por subsector.",
    )
    # Los límites se aplican también en código (el prompt es solo indicación):
    # primero el máximo por subsector y después el tamaño total del conjunto.
    kept_ideas, dropped_sub, dropped_pool = apply_pool_caps(
        output.ideas,
        max_per_subsector=int(ctx.config["max_candidates_per_subsector"]),
        pool_size=int(ctx.config["candidate_pool_size"]),
    )
    if dropped_sub:
        await ctx.event(
            "candidate_cap_subsector",
            f"{dropped_sub} candidatas descartadas por máximo por subsector",
            level="info",
        )
    if dropped_pool:
        await ctx.event(
            "candidate_cap_pool",
            f"{dropped_pool} candidatas descartadas por tamaño del conjunto",
            level="info",
        )
    if len(kept_ideas) < 20:
        await ctx.event(
            "candidate_pool_short",
            f"Solo {len(kept_ideas)} candidatas tras deduplicar y filtrar (<20)",
            level="warning",
        )
    opp_ids: list[uuid.UUID] = []
    for idx, idea in enumerate(kept_ideas):
        fp = deduplication.fingerprint(idea.customer_segment, idea.problem)
        row = await repo.insert_opportunity(
            ctx.session,
            research_run_id=ctx.run_id,
            fingerprint=fp,
            name=_clean_text(idea.name),
            sector_subcategory=_clean_text(idea.sector_subcategory),
            customer_segment=_clean_text(idea.customer_segment),
            buyer=_clean_text(idea.buyer),
            problem=_clean_text(idea.problem),
            solution=_clean_text(idea.solution),
            value_proposition=_clean_text(idea.value_proposition),
            business_model=_clean_text(idea.business_model),
            mvp_scope=clean_json(idea.mvp_scope),
            excluded_features=clean_json(idea.excluded_features),
            acquisition={"channels": clean_json(idea.acquisition_channels)},
            assumptions={"items": clean_json(idea.assumptions)},
            status=OpportunityStatus.CANDIDATE.value,
        )
        if row is not None:
            ctx.idea_index_by_opp[row["id"]] = idx
            opp_ids.append(row["id"])
    await ctx.session.commit()

    candidate_lines: list[str] = []
    for oid, idx in sorted(ctx.idea_index_by_opp.items(), key=lambda kv: kv[1]):
        row = await repo.get_opportunity(ctx.session, oid)
        if row is not None:
            candidate_lines.append(f"[{idx}] {_candidate_summary(row)}")
    dedup = await ctx.do_analysis(
        "dedup_screener",
        "Agrupa candidatas equivalentes. Candidatas:\n" + "\n".join(candidate_lines),
    )
    # Los grupos del modelo refieren índices de la lista de ideas; el código
    # conserva la más completa y marca el resto como duplicate.
    kept: set[int] = set()
    for group in dedup.groups:
        members = [i for i in group if i < len(kept_ideas)]
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
    return {
        "generated": len(output.ideas),
        "kept": len(kept_ideas),
        "opportunity_ids": [str(i) for i in opp_ids],
    }


# ------------------------------- ETAPA 4 -------------------------------------


async def stage_eligibility(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.ELIGIBILITY_FILTER

    async def assess(opp_id: uuid.UUID) -> None:
        async with ctx.semaphore, ctx.session_factory() as task_session:
            tctx = dataclasses.replace(ctx, session=task_session)
            await tctx.check_cancel()
            idx = ctx.idea_index_by_opp[opp_id]
            opp_row = await repo.get_opportunity(task_session, opp_id)
            assert opp_row is not None
            out = await tctx.do_analysis(
                "eligibility_assessor",
                f"[idea_index={idx}] Candidata: {_candidate_summary(opp_row)}",
                opp=opp_id,
            )
            assessments, missing = eligibility.consolidate(
                [
                    eligibility.RuleAssessment(
                        rule=RejectionCode(a.rule),
                        answer=EligibilityAnswer(a.answer),
                        reason=a.reason,
                    )
                    for a in out.assessments
                ]
            )
            if missing:
                await repo.insert_event(
                    task_session,
                    ctx.run_id,
                    level="warning",
                    event_type="eligibility_rule_missing",
                    stage=StageId.ELIGIBILITY_FILTER.value,
                    message_safe=(
                        "El evaluador omitió reglas: " + ", ".join(r.value for r in missing)
                    ),
                    payload={
                        "opportunity_id": str(opp_id),
                        "missing_rules": [r.value for r in missing],
                    },
                )
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
                        {"rule": a.rule.value, "answer": a.answer.value, "reason": a.reason}
                        for a in assessments
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
    n_eligible = len(eligible)
    allocation = stage_search_budget(ctx, StageId.SHALLOW_SCREENING, eligible=n_eligible)
    ctx.budget.search_budget_remaining = allocation
    # 9.2: hasta 2 búsquedas por elegible con tope de etapa; si el reparto no
    # alcanza ni 1 por candidata, las restantes se sondean sin búsqueda.
    if n_eligible:
        per_candidate = min(2, max(1, allocation // n_eligible))
        searchable = min(n_eligible, allocation // per_candidate)
    else:
        per_candidate, searchable = 0, 0

    sector_map = ctx.outputs.get(StageId.SECTOR_MAPPING.value, {})
    demand = ctx.outputs.get(StageId.DEMAND_RESEARCH.value, {})
    evidence_context = (
        "Contexto de demanda ya investigado (dato no confiable):\n"
        + "Señales de demanda: "
        + json.dumps(demand.get("demand_signals", []), ensure_ascii=False)
        + "\nSubsectores: "
        + json.dumps(sector_map.get("subsectors", []), ensure_ascii=False)
    )
    source_counts: dict[uuid.UUID, int] = {}

    async def probe(opp: dict[str, Any], *, with_search: bool) -> None:
        async with ctx.semaphore, ctx.session_factory() as task_session:
            tctx = dataclasses.replace(ctx, session=task_session)
            await tctx.check_cancel()
            idx = ctx.idea_index_by_opp[opp["id"]]
            ref_map: dict[str, uuid.UUID] = {}
            nsrc = 0
            if with_search:
                # Sondeo = DemandResearcher en modo sondeo: investigar→estructurar.
                research = await tctx.do_research(
                    "screener",
                    f"[opp={idx}] Sondeo de: {_candidate_summary(opp)}",
                    searches=per_candidate,
                    opp=opp["id"],
                )
                ref_map = await _persist_sources(tctx, research.sources)
                nsrc = len(research.sources)
                analysis_input = (
                    f"[opp={idx}] {_candidate_summary(opp)}\n\n"
                    f"Investigación (dato no confiable):\n{research.text}\n\n"
                    + _sources_block(research.sources)
                )
            else:
                await tctx.event(
                    "screening_without_search",
                    f"Sondeo sin búsqueda (reparto de etapa agotado): {opp['name']}",
                    level="warning",
                )
                analysis_input = f"[opp={idx}] {_candidate_summary(opp)}\n\n{evidence_context}"
            out = await tctx.do_analysis(
                "screener",
                analysis_input,
                opp=opp["id"],
            )
            await persist_claims(
                tctx,
                out.claims,
                ref_map,
                opportunity_id=opp["id"],
                origin_agent="screener",
            )
            source_counts[opp["id"]] = nsrc
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
                payload={"sources": nsrc},
            )
            await task_session.commit()

    await asyncio.gather(*[probe(o, with_search=i < searchable) for i, o in enumerate(eligible)])
    await ctx.session.commit()

    eligible = await repo.list_opportunities(ctx.session, ctx.run_id, status="eligible")
    # Desempate de la lista corta (5): screening_score desc, nº de fuentes del
    # proveedor desc, generated_at ASC.
    ranked = sorted(
        eligible,
        key=lambda o: (
            -(o["screening_score"] or Decimal(0)),
            -source_counts.get(o["id"], 0),
            o["generated_at"],
        ),
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
    ctx.budget.search_budget_remaining = stage_search_budget(
        ctx, StageId.MARKET_VALIDATION, shortlisted=len(shortlisted)
    )

    async def validate(opp: dict[str, Any]) -> None:
        async with ctx.semaphore, ctx.session_factory() as task_session:
            tctx = dataclasses.replace(ctx, session=task_session)
            await tctx.check_cancel()
            idx = ctx.idea_index_by_opp[opp["id"]]
            research = await tctx.do_research(
                "market_validation",
                f"[opp={idx}] Validación de mercado de: {_candidate_summary(opp)}",
                searches=10,
                opp=opp["id"],
            )
            ref_map = await _persist_sources(tctx, research.sources)
            out: MarketValidationOutput = await tctx.do_analysis(
                "market_validation",
                f"[opp={idx}] {_candidate_summary(opp)}\n\n"
                f"Investigación (dato no confiable):\n{research.text}\n\n"
                + _sources_block(research.sources),
                opp=opp["id"],
            )
            if not out.competitors and tctx.budget.reserve_available(opp["id"]):
                # Una ampliación por oportunidad con cargo a la reserva (9.2).
                research = await tctx.do_research(
                    "market_validation",
                    f"[opp={idx}] Ampliación de búsqueda de competidores para: "
                    + _candidate_summary(opp),
                    searches=_RESERVE_SEARCHES_PER_OPP,
                    opp=opp["id"],
                    charge_reserve=True,
                )
                ref_map = await _persist_sources(tctx, research.sources)
                out = await tctx.do_analysis(
                    "market_validation",
                    f"[opp={idx}] {_candidate_summary(opp)}\n\n"
                    f"Investigación (dato no confiable):\n{research.text}\n\n"
                    + _sources_block(research.sources),
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
                    name=_clean_text(comp.name),
                    website_url=comp.website_url,
                    category=_clean_text(comp.category),
                    positioning=_clean_text(comp.positioning),
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
            # La penetración solo se calcula si la afirmación del universo de
            # compradores queda `verified` tras la auditoría (se marca en la
            # etapa 7); aquí se persiste el universo declarado.
            universe_claim_id = (
                claim_ids[out.buyer_universe_claim_index]
                if out.buyer_universe_claim_index is not None
                and out.buyer_universe_claim_index < len(claim_ids)
                else None
            )
            ctx.pending_universe_claims[opp["id"]] = (
                universe_claim_id,
                out.buyer_universe_customers,
            )
            await task_session.commit()
            await repo.update_opportunity(
                task_session,
                opp["id"],
                acquisition={
                    "channels": (opp.get("acquisition") or {}).get("channels", []),
                    "notes": out.acquisition,
                },
                unit_economics={
                    "hypothesis": True,
                    "price_eur_month": str(price),
                    "variable_cost_eur_month": str(var),
                    "fixed_cost_eur_month": str(fixed),
                    "required_paying_customers": customers,
                    "buyer_universe_customers": out.buyer_universe_customers,
                    "penetration_pct": None,
                },
                risks={
                    "technical_notes": out.technical_notes,
                    "data_barriers": out.data_barriers,
                    "legal_operational_risks": out.legal_operational_risks,
                    "llm_substitution_risk": out.llm_substitution_risk,
                    "estimates": {
                        "technical_scope": out.technical_scope_estimate,
                        "maintenance_cost": out.maintenance_cost_estimate,
                        "mvp_weeks": out.mvp_weeks_estimate,
                    },
                },
            )
            await task_session.commit()

    await asyncio.gather(*[validate(o) for o in shortlisted])
    await ctx.session.commit()
    return {"validated": len(shortlisted)}


# ------------------------------- ETAPA 7 -------------------------------------


def price_status(comp: dict[str, Any], claim: dict[str, Any] | None) -> str:
    """price_status de un competidor (4.3/5): `verified` exige afirmación
    `verified` enlazada e importe+moneda+unidad+país; sin precio -> not_public."""
    full_price = (
        comp["price_value"] is not None
        and bool(comp["price_currency"])
        and bool(comp["price_unit"])
        and bool(comp["price_country"])
    )
    if not full_price:
        return "not_public" if comp["price_value"] is None else "unverified"
    if claim and claim["verification_status"] == VerificationStatus.VERIFIED.value:
        return "verified"
    return "unverified"


async def stage_evidence_audit(ctx: PipelineContext) -> dict[str, Any]:
    ctx.stage = StageId.EVIDENCE_AUDIT
    all_claims = await repo.list_claims(ctx.session, ctx.run_id)
    await audit_claims(ctx, [c["id"] for c in all_claims])
    all_claims = await repo.list_claims(ctx.session, ctx.run_id)

    # Precio verificado solo con afirmación `verified` Y precio completo
    # (importe, moneda, unidad y país); competidor sin precio nunca lo recibe.
    shortlisted = await repo.list_opportunities(ctx.session, ctx.run_id, status="shortlisted")
    claims_by_id = {c["id"]: c for c in all_claims}
    for opp in shortlisted:
        comps = await repo.list_competitors(ctx.session, opp["id"])
        for comp in comps:
            claim = claims_by_id.get(comp["price_claim_id"]) if comp["price_claim_id"] else None
            status = price_status(comp, claim)
            await repo.update_competitor(ctx.session, comp["id"], price_status=status)
        # % de penetración solo si el universo de compradores quedó `verified` (5.3).
        econ = opp.get("unit_economics") or {}
        universe_claim_id, universe_size = ctx.pending_universe_claims.get(opp["id"], (None, None))
        penetration: Decimal | None = None
        if universe_claim_id and universe_size:
            uclaim = claims_by_id.get(universe_claim_id)
            required = econ.get("required_paying_customers")
            if (
                uclaim
                and uclaim["verification_status"] == VerificationStatus.VERIFIED.value
                and required
                and universe_size > 0
            ):
                penetration = (Decimal(required) / Decimal(universe_size) * 100).quantize(
                    Decimal("0.01")
                )
        if "buyer_universe_customers" in econ:
            econ["penetration_pct"] = str(penetration) if penetration is not None else None
            await repo.update_opportunity(ctx.session, opp["id"], unit_economics=econ)
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


async def _audited_claim_block(
    ctx: PipelineContext, opp_id: uuid.UUID
) -> tuple[str, dict[str, uuid.UUID]]:
    """Mapa C1…Cn -> claim_id con el contenido real auditado que recibe el
    CriterionAssessor (4.1): texto, tipo, criterio, estado y disputa."""
    claims = await repo.list_claims(ctx.session, ctx.run_id, opportunity_id=opp_id)
    claims = sorted(claims, key=lambda c: (c["stage"], c["text"]))
    lines: list[str] = []
    ref_map: dict[str, uuid.UUID] = {}
    for i, c in enumerate(claims, start=1):
        ref = f"C{i}"
        ref_map[ref] = c["id"]
        lines.append(
            f"{ref} | criterion={c['criterion_code']} | kind={c['kind']} | "
            f"status={c['verification_status']} | disputed={str(c['disputed']).lower()}"
            f": {c['text']}"
        )
    return "Afirmaciones auditadas:\n" + "\n".join(lines), ref_map


async def _score_once(
    ctx: PipelineContext, opp_id: uuid.UUID, revision: str
) -> dict[str, int] | None:
    idx = ctx.idea_index_by_opp.get(opp_id)
    block, claim_refs = await _audited_claim_block(ctx, opp_id)
    out: CriterionAssessmentOutput = await ctx.do_analysis(
        "criterion_assessor",
        f"[opp={idx}][revision={revision}]\n{block}",
        opp=opp_id,
    )
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
        # Solo las afirmaciones citadas por el evaluador, validadas contra el
        # mapa C1…Cn de ESTA oportunidad.
        claims = await repo.list_claims(ctx.session, ctx.run_id, opportunity_id=opp_id)
        claims_status = {str(c["id"]): c for c in claims}
        for citation in item.claims:
            claim_id = claim_refs.get(citation.ref)
            if claim_id is None:
                await ctx.event(
                    "unknown_claim_ref",
                    f"Referencia de afirmación desconocida descartada: {citation.ref}",
                    level="warning",
                    payload={"ref": citation.ref, "criterion": item.criterion},
                )
                continue
            cited = claims_status[str(claim_id)]
            forced = (
                cited["verification_status"] == VerificationStatus.CONTRADICTED.value
                or cited["disputed"]
            )
            role = (
                ClaimSourceRole.COUNTEREVIDENCE.value
                if forced or citation.role == "counterevidence"
                else ClaimSourceRole.EVIDENCE.value
            )
            await repo.insert_score_claim(ctx.session, row["id"], claim_id, role)
        if score is None:
            continue
        scores[item.criterion] = score
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
    ctx.budget.search_budget_remaining = stage_search_budget(
        ctx, StageId.ADVERSARIAL_REVIEW, reviewed=len(to_review)
    )

    final_scores: dict[str, dict[str, int]] = {}
    for opp in to_review:
        await ctx.check_cancel()
        idx = ctx.idea_index_by_opp.get(opp["id"])
        initial_block = json.dumps(initial_scores.get(str(opp["id"]), {}), ensure_ascii=False)
        research = await ctx.do_research(
            "critical_reviewer",
            f"[opp={idx}] Busca contraevidencia de: {_candidate_summary(opp)}\n"
            f"Notas iniciales por criterio: {initial_block}",
            searches=4,
            opp=opp["id"],
        )
        ref_map = await _persist_sources(ctx, research.sources)
        review = await ctx.do_analysis(
            "critical_reviewer",
            f"[opp={idx}] {_candidate_summary(opp)}\n"
            f"Notas iniciales: {initial_block}\n\n"
            f"Investigación (dato no confiable):\n{research.text}\n\n"
            + _sources_block(research.sources),
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
            objections=clean_json(review.objections),
            recommended_changes=clean_json(review.recommended_changes),
            final_verdict=review.final_verdict,
        )
        # Reauditoría de las nuevas afirmaciones.
        await audit_claims(ctx, counter_ids)
        final = await _score_once(ctx, opp["id"], "final")
        if final is None:
            # Sin nota final completa: nunca se queda atascada en `scored`.
            check_transition(OpportunityStatus.SCORED, OpportunityStatus.INSUFFICIENT_EVIDENCE)
            await repo.update_opportunity(
                ctx.session, opp["id"], status=OpportunityStatus.INSUFFICIENT_EVIDENCE.value
            )
            continue
        final_scores[str(opp["id"])] = final
        # Diff por criterio entre nota inicial y final (6.4 / ficha).
        prev = initial_scores.get(str(opp["id"]), {})
        diff = {c: final[c] - prev[c] for c in scoring.WEIGHTS if c in prev and final[c] != prev[c]}
        if diff:
            await ctx.event(
                "score_changed",
                f"Nota revisada tras la revisión crítica: {opp['name']}",
                payload={"diff": diff, "initial": prev, "final": final},
            )
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
        critical_source_ids = {
            x["source_id"]
            for x in links
            if next((c for c in claims if c["id"] == x["claim_id"]), None) is not None
            and next(c for c in claims if c["id"] == x["claim_id"])["criterion_code"]
            in scoring.CRITICAL
        }
        critical_domains: set[str] = set()
        contradicted_central = False
        for c in claims:
            if c["verification_status"] == VerificationStatus.CONTRADICTED.value and (
                c["criterion_code"] in scoring.CRITICAL
            ):
                contradicted_central = True
        for sid in critical_source_ids:
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

    # Desempate 6.4: nota desc, confianza desc, demand desc, generated_at ASC.
    confidence_rank = {"high": 2, "medium": 1, "low": 0, None: -1}
    qualifying.sort(
        key=lambda pair: (
            -pair[1],
            -confidence_rank.get(pair[0]["confidence"], -1),
            -final_scores[str(pair[0]["id"])]["demand"],
            pair[0]["generated_at"],
        )
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
    # Las cualificadas que exceden requested_ideas se quedan en `scored` y se
    # exponen como `qualified_not_published` (ver DECISIONS, D13).
    qualified_not_published = len(qualifying) - published
    await ctx.session.commit()
    stats = await repo.opportunity_status_counts(ctx.session, ctx.run_id)
    stats["qualified_not_published"] = qualified_not_published
    await _assert_terminal_invariants(ctx, qualified_not_published)
    return {"published": published, "statistics": stats}


async def _assert_terminal_invariants(ctx: PipelineContext, qualified_not_published: int) -> None:
    """Invariante final: nada queda en candidate/eligible/shortlisted y `scored`
    solo describe a cualificadas no publicadas."""
    counts = await repo.opportunity_status_counts(ctx.session, ctx.run_id)
    stuck = {s: counts.get(s, 0) for s in ("candidate", "eligible", "shortlisted")}
    stuck = {s: n for s, n in stuck.items() if n}
    scored_left = counts.get("scored", 0)
    if stuck or scored_left != qualified_not_published:
        await ctx.event(
            "invariant_violation",
            "Invariante de estados finales incumplido",
            level="error",
            payload={"stuck": stuck, "scored": scored_left},
        )
        raise RuntimeError(f"Invariante final incumplido: {stuck} scored={scored_left}")
