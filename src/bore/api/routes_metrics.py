"""GET /api/v1/metrics — métricas 9.3 calculadas desde la BD (añadido a 8.1,
ver DECISIONS)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import tables as t
from bore.db.session import new_session

router = APIRouter()


async def _session() -> Any:
    async with new_session() as s:
        yield s


@router.get("/metrics")
async def metrics(session: AsyncSession = Depends(_session)) -> dict[str, Any]:  # noqa: B008
    runs_by_status = {
        r.status: r.n
        for r in (
            await session.execute(
                sa.select(t.research_runs.c.status, sa.func.count().label("n")).group_by(
                    t.research_runs.c.status
                )
            )
        ).all()
    }
    stage_duration = {
        r.stage: float(r.avg_ms or 0)
        for r in (
            await session.execute(
                sa.select(
                    t.stage_runs.c.stage,
                    sa.func.avg(
                        sa.func.extract(
                            "epoch",
                            t.stage_runs.c.finished_at - t.stage_runs.c.started_at,
                        )
                        * 1000
                    ).label("avg_ms"),
                )
                .where(t.stage_runs.c.finished_at.is_not(None))
                .group_by(t.stage_runs.c.stage)
            )
        ).all()
    }
    cost = (
        await session.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(t.provider_calls.c.cost_usd), 0),
                sa.func.coalesce(
                    sa.func.sum(sa.case((t.provider_calls.c.cost_estimated.is_(True), 1), else_=0)),
                    0,
                ),
            )
        )
    ).one()
    src_valid = (
        await session.execute(
            sa.select(
                sa.func.count().filter(t.sources.c.access_status == "ok"),
                sa.func.count(),
            )
        )
    ).one()
    claim_counts = (
        await session.execute(
            sa.select(
                sa.func.count().filter(t.claims.c.verification_status == "unverified"),
                sa.func.count(),
            )
        )
    ).one()
    opp_counts = (
        await session.execute(
            sa.select(
                sa.func.count().filter(t.opportunities.c.status == "published"),
                sa.func.count().filter(t.opportunities.c.status.in_(("published", "scored"))),
                sa.func.count().filter(t.opportunities.c.status == "duplicate"),
                sa.func.count(),
            )
        )
    ).one()
    discarded_refs = (
        await session.execute(
            sa.select(sa.func.count()).where(
                t.run_events.c.event_type.in_(("unknown_source_ref", "unknown_claim_ref"))
            )
        )
    ).scalar_one()
    total_claims = claim_counts[1] or 1
    total_opps = opp_counts[3] or 1
    return {
        "runs_by_status": runs_by_status,
        "stage_duration_avg_ms": stage_duration,
        "cost": {
            "total_usd": str(cost[0]),
            "estimated_calls": int(cost[1]),
        },
        "sources": {"valid": src_valid[0], "total": src_valid[1]},
        "claims": {
            "unverified_pct": round(claim_counts[0] / total_claims * 100, 2),
            "total": claim_counts[1],
        },
        "opportunities": {
            "qualified_ratio": round(opp_counts[1] / total_opps, 4),
            "published": opp_counts[0],
            "duplicates": opp_counts[2],
            "total": opp_counts[3],
        },
        "unknown_refs_discarded": discarded_refs,
    }
