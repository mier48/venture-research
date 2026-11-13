"""Endpoints de ejecuciones de investigación (8.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from bore.agents.prompt_registry import composite_prompt_version, prompt_versions
from bore.api.errors import ApiError
from bore.api.schemas import (
    OpportunitySummary,
    ResearchRunCreate,
    ResearchRunCreated,
    RunDetail,
)
from bore.config import Settings, get_settings
from bore.db import repositories as repo
from bore.db.session import new_session
from bore.domain.scoring import RUBRIC_VERSION
from bore.orchestration import jobs
from bore.orchestration.budgets import load_pricing

router = APIRouter()

_TERMINAL = {"completed", "partial", "failed", "cancelled"}


async def _session() -> Any:
    async with new_session() as s:
        yield s


@router.post("/research-runs", status_code=202)
async def create_run(
    body: ResearchRunCreate,
    request: Request,
    session: AsyncSession = Depends(_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> JSONResponse:
    if not body.offline:
        from bore.providers.openai_research import check_models_exist_cached

        problems = await check_models_exist_cached(settings)
        if problems:
            raise ApiError(
                503,
                "provider_not_configured",
                "Proveedor no configurado",
                "Modo real no disponible: configura OPENAI_API_KEY y BORE_MODEL_* "
                "o usa offline=true.",
            )
    if await repo.count_active_runs(session) > 0:
        raise ApiError(
            409,
            "run_already_active",
            "Ya hay una ejecución activa",
            "Espera a que termine la ejecución en curso.",
        )
    last = await repo.last_run_created_at(session)
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - last).total_seconds()
        if elapsed < settings.bore_min_seconds_between_runs:
            raise ApiError(409, "rate_limited", "Demasiadas ejecuciones seguidas")
    daily = await repo.daily_cost_usd(session)
    if daily >= Decimal(str(settings.bore_max_usd_per_day)):
        raise ApiError(
            409,
            "daily_budget_exceeded",
            "Límite diario de gasto alcanzado",
        )
    pricing = load_pricing()
    config = body.to_config()
    run = await repo.create_run(
        session,
        sector=body.sector,
        country=body.country,
        language=body.language,
        config=config,
        is_synthetic=body.offline,
        model_config={
            "research": settings.bore_model_research or None,
            "analysis": settings.bore_model_analysis or None,
            "review": settings.bore_model_review or None,
            "prompt_versions": prompt_versions(),
        },
        prompt_version=composite_prompt_version(),
        rubric_version=RUBRIC_VERSION,
        pricing_version=pricing.pricing_version,
    )
    await session.commit()
    status = run["status"]
    try:
        job = jobs.enqueue_run(run["id"], config, redis_url=settings.redis_url)
        await repo.update_run(session, run["id"], rq_job_id=job.id)
        await session.commit()
    except Exception:
        await repo.update_run(session, run["id"], status="failed", error_code="enqueue_failed")
        await session.commit()
        status = "failed"
    return JSONResponse(
        status_code=202,
        content=ResearchRunCreated(
            id=str(run["id"]),
            status=status,
            current_stage=None,
            progress_percent=0,
        ).model_dump(),
    )


@router.get("/research-runs")
async def list_runs(
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> list[dict[str, Any]]:
    runs = await repo.list_runs(session, status=status, limit=limit, offset=offset)
    return [
        {
            "id": str(r["id"]),
            "sector": r["sector"],
            "status": r["status"],
            "is_synthetic": r["is_synthetic"],
            "progress_percent": r["progress_percent"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in runs
    ]


async def _get_or_404(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    run = await repo.get_run(session, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Ejecución no encontrada")
    return run


@router.get("/research-runs/{run_id}")
async def run_detail(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> RunDetail:
    run = await _get_or_404(session, run_id)
    stats = await repo.opportunity_status_counts(session, run_id)
    published = await repo.list_opportunities(session, run_id, status="published")
    return RunDetail(
        id=str(run["id"]),
        sector=run["sector"],
        country=run["country"],
        status=run["status"],
        stop_reason=run["stop_reason"],
        current_stage=run["current_stage"],
        progress_percent=run["progress_percent"],
        is_synthetic=run["is_synthetic"],
        error_code=run["error_code"],
        error_message_safe=run["error_message_safe"],
        statistics=stats,
        cost={
            "actual_usd": float(run["actual_cost_usd"] or 0),
            "estimated": bool(run["cost_is_estimated"]),
            "web_search_calls": run["web_search_calls"],
            "model_calls": run["model_calls"],
        },
        opportunities=[
            OpportunitySummary(
                id=str(o["id"]),
                name=o["name"],
                status=o["status"],
                final_score=float(o["final_score"]) if o["final_score"] is not None else None,
                confidence=o["confidence"],
                rank=o["rank"],
            )
            for o in published
        ],
        created_at=run["created_at"].isoformat() if run["created_at"] else "",
        finished_at=run["finished_at"].isoformat() if run["finished_at"] else None,
    )


@router.get("/research-runs/{run_id}/opportunities")
async def run_opportunities(
    run_id: uuid.UUID,
    status: str | None = None,
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> list[dict[str, Any]]:
    await _get_or_404(session, run_id)
    opps = await repo.list_opportunities(session, run_id, status=status)
    return [
        {
            "id": str(o["id"]),
            "name": o["name"],
            "status": o["status"],
            "sector_subcategory": o["sector_subcategory"],
            "final_score": float(o["final_score"]) if o["final_score"] is not None else None,
            "confidence": o["confidence"],
            "rank": o["rank"],
            "rejection_code": o["rejection_code"],
        }
        for o in opps
    ]


@router.get("/research-runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID,
    after_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> dict[str, Any]:
    await _get_or_404(session, run_id)
    events = await repo.list_events(session, run_id, after_id=after_id, limit=limit)
    return {
        "events": [
            {
                "id": str(e["id"]),
                "timestamp": e["timestamp"].isoformat() if e["timestamp"] else None,
                "level": e["level"],
                "event_type": e["event_type"],
                "stage": e["stage"],
                "message": e["message_safe"],
            }
            for e in events
        ],
        "next_cursor": str(events[-1]["id"]) if events else None,
    }


@router.post("/research-runs/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> dict[str, str]:
    run = await _get_or_404(session, run_id)
    if run["status"] in _TERMINAL:
        raise ApiError(409, "run_finished", "La ejecución ya terminó")
    await repo.request_cancel(session, run_id)
    await session.commit()
    return {"status": "cancel_requested"}


@router.get("/research-runs/{run_id}/export")
async def export_run(
    run_id: uuid.UUID,
    format: str = Query(default="md", pattern="^(md|json)$"),
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> Response:
    run = await _get_or_404(session, run_id)
    headers = {"content-disposition": f'attachment; filename="bore-{run_id}.{format}"'}
    if format == "json":
        from bore.reporting.json_export import export_json

        content = await export_json(session, run)
        return Response(content=content, media_type="application/json", headers=headers)
    from bore.reporting.markdown import export_markdown

    content = await export_markdown(session, run)
    return Response(content=content, media_type="text/markdown; charset=utf-8", headers=headers)


@router.delete("/research-runs/{run_id}", status_code=204)
async def delete_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(_session),  # noqa: B008
) -> Response:
    run = await _get_or_404(session, run_id)
    if run["status"] not in _TERMINAL:
        raise ApiError(409, "run_active", "No se puede borrar una ejecución en curso")
    await repo.delete_run(session, run_id)
    await session.commit()
    return Response(status_code=204)
