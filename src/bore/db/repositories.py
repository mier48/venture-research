"""Repositorio: inserciones idempotentes ON CONFLICT y consultas de la API."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import tables as t


def _row_dict(row: sa.Row[Any]) -> dict[str, Any]:
    return dict(row._mapping)


# ------------------------------- research_runs -------------------------------


async def create_run(
    s: AsyncSession,
    *,
    sector: str,
    country: str,
    language: str,
    config: dict[str, Any],
    is_synthetic: bool,
    model_config: dict[str, Any] | None,
    prompt_version: str,
    rubric_version: str,
    pricing_version: str,
) -> dict[str, Any]:
    run_id = uuid.uuid4()
    stmt = (
        pg_insert(t.research_runs)
        .values(
            id=run_id,
            sector=sector,
            country=country,
            language=language,
            config=config,
            status="queued",
            is_synthetic=is_synthetic,
            model_config=model_config,
            prompt_version=prompt_version,
            rubric_version=rubric_version,
            pricing_version=pricing_version,
        )
        .returning(t.research_runs)
    )
    row = (await s.execute(stmt)).one()
    return _row_dict(row)


async def get_run(s: AsyncSession, run_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await s.execute(sa.select(t.research_runs).where(t.research_runs.c.id == run_id))
    ).one_or_none()
    return _row_dict(row) if row else None


async def list_runs(
    s: AsyncSession, *, status: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    q = sa.select(t.research_runs).order_by(t.research_runs.c.created_at.desc())
    if status:
        q = q.where(t.research_runs.c.status == status)
    q = q.limit(limit).offset(offset)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def count_active_runs(s: AsyncSession) -> int:
    q = sa.select(sa.func.count()).where(t.research_runs.c.status.in_(("queued", "running")))
    return int((await s.execute(q)).scalar_one())


async def last_run_created_at(s: AsyncSession) -> datetime | None:
    q = sa.select(sa.func.max(t.research_runs.c.created_at))
    value: datetime | None = (await s.execute(q)).scalar_one()
    return value


async def daily_cost_usd(s: AsyncSession, day: date | None = None) -> Decimal:
    day = day or datetime.now(UTC).date()
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    q = sa.select(sa.func.coalesce(sa.func.sum(t.provider_calls.c.cost_usd), 0)).where(
        t.provider_calls.c.created_at >= start
    )
    return Decimal((await s.execute(q)).scalar_one())


async def update_run(s: AsyncSession, run_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(
        sa.update(t.research_runs).where(t.research_runs.c.id == run_id).values(**fields)
    )


async def heartbeat(s: AsyncSession, run_id: uuid.UUID) -> None:
    await update_run(s, run_id, heartbeat_at=datetime.now(UTC))


async def request_cancel(s: AsyncSession, run_id: uuid.UUID) -> None:
    await update_run(s, run_id, cancel_requested_at=datetime.now(UTC))


async def is_cancel_requested(s: AsyncSession, run_id: uuid.UUID) -> bool:
    q = sa.select(t.research_runs.c.cancel_requested_at).where(t.research_runs.c.id == run_id)
    return (await s.execute(q)).scalar_one() is not None


async def delete_run(s: AsyncSession, run_id: uuid.UUID) -> None:
    await s.execute(sa.delete(t.research_runs).where(t.research_runs.c.id == run_id))


async def runs_without_heartbeat(s: AsyncSession, *, older_than: datetime) -> list[dict[str, Any]]:
    q = sa.select(t.research_runs).where(
        t.research_runs.c.status == "running",
        sa.or_(
            t.research_runs.c.heartbeat_at.is_(None),
            t.research_runs.c.heartbeat_at < older_than,
        ),
    )
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def orphan_queued_runs(s: AsyncSession, *, older_than: datetime) -> list[dict[str, Any]]:
    """Ejecuciones `queued` más antiguas que el umbral (posibles olvidadas)."""
    q = sa.select(t.research_runs).where(
        t.research_runs.c.status == "queued",
        t.research_runs.c.created_at < older_than,
    )
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def recompute_run_costs(s: AsyncSession, run_id: uuid.UUID) -> None:
    """Los totales de research_runs se derivan de provider_calls."""
    q = sa.select(
        sa.func.coalesce(sa.func.sum(t.provider_calls.c.cost_usd), 0),
        sa.func.coalesce(sa.func.sum(t.provider_calls.c.tokens_input), 0),
        sa.func.coalesce(sa.func.sum(t.provider_calls.c.tokens_output), 0),
        sa.func.coalesce(sa.func.sum(t.provider_calls.c.web_search_calls), 0),
        sa.func.coalesce(sa.func.sum(t.provider_calls.c.requests), 0),
        sa.func.bool_or(t.provider_calls.c.cost_estimated),
    ).where(t.provider_calls.c.research_run_id == run_id)
    cost, tin, tout, wsc, reqs, estimated = (await s.execute(q)).one()
    await update_run(
        s,
        run_id,
        actual_cost_usd=cost,
        tokens_input=tin,
        tokens_output=tout,
        web_search_calls=wsc,
        model_calls=reqs,
        cost_is_estimated=bool(estimated),
    )


# -------------------------------- stage_runs ---------------------------------


async def get_stage_run(
    s: AsyncSession, run_id: uuid.UUID, stage: str, attempt: int
) -> dict[str, Any] | None:
    row = (
        await s.execute(
            sa.select(t.stage_runs).where(
                t.stage_runs.c.research_run_id == run_id,
                t.stage_runs.c.stage == stage,
                t.stage_runs.c.attempt == attempt,
            )
        )
    ).one_or_none()
    return _row_dict(row) if row else None


async def insert_stage_run(s: AsyncSession, **fields: Any) -> dict[str, Any]:
    stmt = (
        pg_insert(t.stage_runs)
        .values(id=uuid.uuid4(), **fields)
        .on_conflict_do_nothing(constraint="uq_stage_runs_run_stage_attempt")
        .returning(t.stage_runs)
    )
    row = (await s.execute(stmt)).one_or_none()
    if row is None:
        existing = await get_stage_run(
            s, fields["research_run_id"], fields["stage"], fields["attempt"]
        )
        assert existing is not None
        return existing
    return _row_dict(row)


async def finish_stage_run(
    s: AsyncSession,
    stage_run_id: uuid.UUID,
    *,
    status: str,
    output: Any,
    error_code: str | None,
    usage: Any,
) -> None:
    await s.execute(
        sa.update(t.stage_runs)
        .where(t.stage_runs.c.id == stage_run_id)
        .values(
            status=status,
            finished_at=datetime.now(UTC),
            output=output,
            error_code=error_code,
            usage=usage,
        )
    )


# ------------------------------- provider_calls ------------------------------


async def insert_provider_call(s: AsyncSession, **fields: Any) -> None:
    await s.execute(pg_insert(t.provider_calls).values(id=uuid.uuid4(), **fields))


# ------------------------------- opportunities -------------------------------


async def insert_opportunity(s: AsyncSession, **fields: Any) -> dict[str, Any] | None:
    """Idempotente por (research_run_id, fingerprint). Devuelve None si ya existe."""
    stmt = (
        pg_insert(t.opportunities)
        .values(id=uuid.uuid4(), **fields)
        .on_conflict_do_nothing(constraint="uq_opportunities_run_fingerprint")
        .returning(t.opportunities)
    )
    row = (await s.execute(stmt)).one_or_none()
    return _row_dict(row) if row else None


async def get_opportunity(s: AsyncSession, opp_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await s.execute(sa.select(t.opportunities).where(t.opportunities.c.id == opp_id))
    ).one_or_none()
    return _row_dict(row) if row else None


async def list_opportunities(
    s: AsyncSession, run_id: uuid.UUID, *, status: str | None = None
) -> list[dict[str, Any]]:
    q = sa.select(t.opportunities).where(t.opportunities.c.research_run_id == run_id)
    if status:
        q = q.where(t.opportunities.c.status == status)
    q = q.order_by(t.opportunities.c.rank.asc().nulls_last(), t.opportunities.c.generated_at)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def update_opportunity(s: AsyncSession, opp_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(
        sa.update(t.opportunities).where(t.opportunities.c.id == opp_id).values(**fields)
    )


async def opportunity_status_counts(s: AsyncSession, run_id: uuid.UUID) -> dict[str, int]:
    q = (
        sa.select(t.opportunities.c.status, sa.func.count())
        .where(t.opportunities.c.research_run_id == run_id)
        .group_by(t.opportunities.c.status)
    )
    return {status: int(n) for status, n in (await s.execute(q)).all()}


# -------------------------------- competitors --------------------------------


async def insert_competitor(s: AsyncSession, **fields: Any) -> None:
    await s.execute(pg_insert(t.competitors).values(id=uuid.uuid4(), **fields))


async def update_competitor(s: AsyncSession, comp_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(sa.update(t.competitors).where(t.competitors.c.id == comp_id).values(**fields))


async def list_competitors(s: AsyncSession, opportunity_id: uuid.UUID) -> list[dict[str, Any]]:
    q = sa.select(t.competitors).where(t.competitors.c.opportunity_id == opportunity_id)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


# ---------------------------------- sources ----------------------------------


async def upsert_source(s: AsyncSession, **fields: Any) -> dict[str, Any]:
    """Idempotente por (research_run_id, canonical_url)."""
    stmt = (
        pg_insert(t.sources)
        .values(id=uuid.uuid4(), **fields)
        .on_conflict_do_nothing(constraint="uq_sources_run_canonical")
        .returning(t.sources)
    )
    row = (await s.execute(stmt)).one_or_none()
    if row is not None:
        return _row_dict(row)
    existing = (
        await s.execute(
            sa.select(t.sources).where(
                t.sources.c.research_run_id == fields["research_run_id"],
                t.sources.c.canonical_url == fields["canonical_url"],
            )
        )
    ).one()
    return _row_dict(existing)


async def update_source(s: AsyncSession, source_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(sa.update(t.sources).where(t.sources.c.id == source_id).values(**fields))


async def get_source(s: AsyncSession, source_id: uuid.UUID) -> dict[str, Any] | None:
    row = (await s.execute(sa.select(t.sources).where(t.sources.c.id == source_id))).one_or_none()
    return _row_dict(row) if row else None


async def list_sources(s: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]]:
    q = sa.select(t.sources).where(t.sources.c.research_run_id == run_id)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


# ----------------------------------- claims ----------------------------------


async def insert_claim(s: AsyncSession, claim_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(pg_insert(t.claims).values(id=claim_id, **fields).on_conflict_do_nothing())


async def update_claim(s: AsyncSession, claim_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(sa.update(t.claims).where(t.claims.c.id == claim_id).values(**fields))


async def list_claims(
    s: AsyncSession, run_id: uuid.UUID, *, opportunity_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    q = sa.select(t.claims).where(t.claims.c.research_run_id == run_id)
    if opportunity_id is not None:
        q = q.where(t.claims.c.opportunity_id == opportunity_id)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def insert_claim_source(
    s: AsyncSession, claim_id: uuid.UUID, source_id: uuid.UUID, **fields: Any
) -> None:
    await s.execute(
        pg_insert(t.claim_sources)
        .values(claim_id=claim_id, source_id=source_id, **fields)
        .on_conflict_do_nothing(constraint="uq_claim_sources_pair")
    )


async def update_claim_source(
    s: AsyncSession, claim_id: uuid.UUID, source_id: uuid.UUID, **fields: Any
) -> None:
    await s.execute(
        sa.update(t.claim_sources)
        .where(
            t.claim_sources.c.claim_id == claim_id,
            t.claim_sources.c.source_id == source_id,
        )
        .values(**fields)
    )


async def list_claim_sources(s: AsyncSession, claim_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not claim_ids:
        return []
    q = sa.select(t.claim_sources).where(t.claim_sources.c.claim_id.in_(claim_ids))
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def claim_sources_for_run(s: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]]:
    q = (
        sa.select(t.claim_sources, t.claims.c.opportunity_id)
        .join(t.claims, t.claims.c.id == t.claim_sources.c.claim_id)
        .where(t.claims.c.research_run_id == run_id)
    )
    return [_row_dict(r) for r in (await s.execute(q)).all()]


# ------------------------------- criterion_scores ----------------------------


async def insert_criterion_score(s: AsyncSession, **fields: Any) -> dict[str, Any]:
    stmt = (
        pg_insert(t.criterion_scores)
        .values(id=uuid.uuid4(), **fields)
        .on_conflict_do_nothing(constraint="uq_criterion_scores")
        .returning(t.criterion_scores)
    )
    row = (await s.execute(stmt)).one_or_none()
    if row is not None:
        return _row_dict(row)
    existing = (
        await s.execute(
            sa.select(t.criterion_scores).where(
                t.criterion_scores.c.opportunity_id == fields["opportunity_id"],
                t.criterion_scores.c.rubric_version == fields["rubric_version"],
                t.criterion_scores.c.criterion_code == fields["criterion_code"],
                t.criterion_scores.c.revision == fields["revision"],
            )
        )
    ).one()
    return _row_dict(existing)


async def update_criterion_score(s: AsyncSession, score_id: uuid.UUID, **fields: Any) -> None:
    await s.execute(
        sa.update(t.criterion_scores).where(t.criterion_scores.c.id == score_id).values(**fields)
    )


async def list_criterion_scores(
    s: AsyncSession, opportunity_id: uuid.UUID, *, revision: str | None = None
) -> list[dict[str, Any]]:
    q = sa.select(t.criterion_scores).where(t.criterion_scores.c.opportunity_id == opportunity_id)
    if revision:
        q = q.where(t.criterion_scores.c.revision == revision)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


async def insert_score_claim(
    s: AsyncSession, criterion_score_id: uuid.UUID, claim_id: uuid.UUID, role: str
) -> None:
    await s.execute(
        pg_insert(t.criterion_score_claims)
        .values(criterion_score_id=criterion_score_id, claim_id=claim_id, role=role)
        .on_conflict_do_nothing(constraint="uq_score_claims")
    )


# ----------------------------------- reviews ---------------------------------


async def insert_review(s: AsyncSession, **fields: Any) -> None:
    await s.execute(pg_insert(t.reviews).values(id=uuid.uuid4(), **fields))


async def list_reviews(s: AsyncSession, opportunity_id: uuid.UUID) -> list[dict[str, Any]]:
    q = sa.select(t.reviews).where(t.reviews.c.opportunity_id == opportunity_id)
    return [_row_dict(r) for r in (await s.execute(q)).all()]


# ---------------------------------- run_events -------------------------------


async def insert_event(
    s: AsyncSession,
    run_id: uuid.UUID,
    *,
    level: str,
    event_type: str,
    stage: str | None,
    message_safe: str,
    payload: Any = None,
) -> None:
    await s.execute(
        pg_insert(t.run_events).values(
            id=uuid.uuid4(),
            research_run_id=run_id,
            level=level,
            event_type=event_type,
            stage=stage,
            message_safe=message_safe,
            payload=payload,
        )
    )


async def list_events(
    s: AsyncSession, run_id: uuid.UUID, *, after_id: uuid.UUID | None, limit: int
) -> list[dict[str, Any]]:
    q = (
        sa.select(t.run_events)
        .where(t.run_events.c.research_run_id == run_id)
        .order_by(t.run_events.c.timestamp, t.run_events.c.id)
    )
    if after_id is not None:
        anchor = (
            await s.execute(
                sa.select(t.run_events.c.timestamp, t.run_events.c.id).where(
                    t.run_events.c.id == after_id
                )
            )
        ).one_or_none()
        if anchor:
            q = q.where(
                sa.tuple_(t.run_events.c.timestamp, t.run_events.c.id)
                > sa.tuple_(anchor[0], anchor[1])
            )
    q = q.limit(limit)
    return [_row_dict(r) for r in (await s.execute(q)).all()]
