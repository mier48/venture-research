"""Orquestador del pipeline de 10 etapas (secciones 4 y 9)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bore.db import repositories as repo
from bore.domain.enums import (
    STAGE_ORDER,
    STAGE_PROGRESS_WEIGHTS,
    ErrorCode,
    RunStatus,
    StageId,
    StopReason,
)
from bore.orchestration import budgets as budgets_mod
from bore.orchestration import stages
from bore.orchestration.budgets import (
    BudgetExceeded,
    BudgetManager,
    Pricing,
    load_pricing,
)
from bore.providers.contracts import (
    AnalysisProvider,
    ResearchProvider,
    SourceVerifier,
)
from bore.providers.errors import ProviderError

HEARTBEAT_INTERVAL_SECONDS = 15


async def _heartbeat_loop(session_factory: Any, run_id: uuid.UUID, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with session_factory() as s:
                await repo.heartbeat(s, run_id)
                await s.commit()
        except Exception:  # noqa: S110
            pass
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)


_STAGE_FUNCS = {
    StageId.SECTOR_MAPPING: stages.stage_sector_mapping,
    StageId.DEMAND_RESEARCH: stages.stage_demand_research,
    StageId.CANDIDATE_GENERATION: stages.stage_candidate_generation,
    StageId.ELIGIBILITY_FILTER: stages.stage_eligibility,
    StageId.SHALLOW_SCREENING: stages.stage_screening,
    StageId.MARKET_VALIDATION: stages.stage_market_validation,
    StageId.EVIDENCE_AUDIT: stages.stage_evidence_audit,
    StageId.SCORING: stages.stage_scoring,
}


def _progress(done_stages: int) -> int:
    return min(100, sum(STAGE_PROGRESS_WEIGHTS[:done_stages]))


async def run_pipeline(
    session: AsyncSession,
    session_factory: Any,
    run_id: uuid.UUID,
    config: dict[str, Any],
    *,
    is_synthetic: bool,
    research: ResearchProvider,
    analysis: AnalysisProvider,
    verifier: SourceVerifier,
    pricing: Pricing | None = None,
    provider_name: str = "fake",
    model_by_role: dict[str, str | None] | None = None,
) -> None:
    """Ejecuta el pipeline completo sobre una ejecución `queued`."""
    pricing = pricing or load_pricing()
    started = time.monotonic()
    stage_outputs: dict[str, Any] = {}
    budget = BudgetManager(
        max_usd=Decimal(str(config["limits"]["max_usd"])),
        max_web_search_calls=config["limits"]["max_web_search_calls"],
        max_model_calls=config["limits"]["max_model_calls"],
        elapsed_seconds=lambda: time.monotonic() - started,
        max_duration_seconds=config["limits"]["max_duration_seconds"],
        reserve_remaining=int(
            config["limits"].get("search_reserve", budgets_mod.DEFAULT_SEARCH_SPLIT["reserve"])
        ),
        pricing=pricing,
    )
    ctx = stages.PipelineContext(
        session=session,
        session_factory=session_factory,
        run_id=run_id,
        config=config,
        is_synthetic=is_synthetic,
        research=research,
        analysis=analysis,
        verifier=verifier,
        budget=budget,
        pricing=pricing,
        provider_name=provider_name,
        model_by_role=model_by_role or {},
    )
    ctx.outputs = stage_outputs  # definido abajo antes del bucle

    await repo.update_run(
        session,
        run_id,
        status=RunStatus.RUNNING.value,
        started_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
    )
    await session.commit()

    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(session_factory, run_id, stop_heartbeat))

    final_status = RunStatus.COMPLETED.value
    stop_reason: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    done = 0
    try:
        for stage_index, stage_id in enumerate(STAGE_ORDER):
            await ctx.check_cancel()
            budget.check_time()
            ctx.stage = stage_id
            await repo.update_run(
                session,
                run_id,
                current_stage=stage_id.value,
                progress_percent=_progress(stage_index),
            )
            await session.commit()

            payload = {"run_id": str(run_id), "stage": stage_id.value, "config": config}
            ihash = stages.input_hash(payload)
            existing = await repo.get_stage_run(session, run_id, stage_id.value, 1)
            if existing and existing["status"] == "completed" and existing["input_hash"] == ihash:
                stage_outputs[stage_id.value] = existing["output"]
                done += 1
                continue

            stage_row = await repo.insert_stage_run(
                session,
                research_run_id=run_id,
                stage=stage_id.value,
                attempt=1,
                status="running",
                input_hash=ihash,
            )
            await session.commit()
            try:
                if stage_id is StageId.DEMAND_RESEARCH:
                    output = await stages.stage_demand_research(
                        ctx, stage_outputs.get(StageId.SECTOR_MAPPING.value, {})
                    )
                elif stage_id is StageId.ADVERSARIAL_REVIEW:
                    output = await stages.stage_adversarial_review(
                        ctx, stage_outputs.get("scored", {})
                    )
                elif stage_id is StageId.RANKING_AND_REPORT:
                    output = await stages.stage_ranking(ctx, stage_outputs.get("final_scores", {}))
                else:
                    func: Any = _STAGE_FUNCS.get(stage_id)
                    assert func is not None
                    output = await func(ctx)
            except (BudgetExceeded, stages.PipelineCancelled):
                raise
            except Exception as exc:
                await repo.finish_stage_run(
                    session,
                    stage_row["id"],
                    status="failed",
                    output=None,
                    error_code=ErrorCode.INTERNAL_ERROR.value,
                    usage=None,
                )
                await session.commit()
                raise exc
            await repo.finish_stage_run(
                session,
                stage_row["id"],
                status="completed",
                output=output,
                error_code=None,
                usage=None,
            )
            if stage_id is StageId.SCORING:
                stage_outputs["scored"] = output.get("scored", {})
            if stage_id is StageId.ADVERSARIAL_REVIEW:
                stage_outputs["final_scores"] = output.get("final_scores", {})
            stage_outputs[stage_id.value] = output
            done += 1
            await session.commit()
    except stages.PipelineCancelled:
        final_status = RunStatus.CANCELLED.value
        stop_reason = StopReason.CANCELLED_BY_USER.value
    except BudgetExceeded as exc:
        final_status = RunStatus.PARTIAL.value
        stop_reason = exc.stop_reason.value
        error_msg = exc.detail
        # Se clasifican y publican las que ya tengan nota final (9.2).
        ctx.stage = StageId.RANKING_AND_REPORT
        try:
            await stages.stage_ranking(ctx, stage_outputs.get("final_scores", {}))
            await session.commit()
        except Exception as rank_exc:
            await repo.insert_event(
                session,
                run_id,
                level="warning",
                event_type="ranking_after_cut_failed",
                stage=StageId.RANKING_AND_REPORT.value,
                message_safe=f"No se pudo clasificar tras el corte: {rank_exc}",
                payload=None,
            )
            await session.commit()
    except ProviderError as exc:
        final_status = RunStatus.FAILED.value
        stop_reason = StopReason.PROVIDER_ERROR.value
        error_code = exc.error_code.value
        error_msg = str(exc)[:500]
    except Exception as exc:
        final_status = RunStatus.FAILED.value
        error_code = ErrorCode.INTERNAL_ERROR.value
        error_msg = str(exc)[:500]
    finally:
        stop_heartbeat.set()
        heartbeat_task.cancel()
        ctx.stage = None
        # Si una sentencia falló a media etapa, la transacción queda abortada:
        # rollback previo para poder registrar el estado final de la ejecución.
        await session.rollback()
        await repo.recompute_run_costs(session, run_id)
        extra: dict[str, Any] = {}
        trace_id = getattr(research, "last_trace_id", None)
        if trace_id:
            extra["trace_id"] = trace_id
        await repo.update_run(
            session,
            run_id,
            status=final_status,
            **extra,
            stop_reason=stop_reason,
            finished_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            progress_percent=100 if final_status == RunStatus.COMPLETED.value else _progress(done),
            error_code=error_code,
            error_message_safe=error_msg,
        )
        await repo.insert_event(
            session,
            run_id,
            level="info" if final_status == RunStatus.COMPLETED.value else "warning",
            event_type="run_finished",
            stage=None,
            message_safe=f"Ejecución terminada: {final_status}",
            payload={"stop_reason": stop_reason, "error_code": error_code},
        )
        await session.commit()


async def mark_orphaned_runs(session: AsyncSession, *, max_age_seconds: int = 60) -> int:
    """Ejecuciones `running` sin latido reciente -> failed con worker_lost."""
    threshold = datetime.now(UTC).timestamp() - max_age_seconds
    threshold_dt = datetime.fromtimestamp(threshold, tz=UTC)
    orphans = await repo.runs_without_heartbeat(session, older_than=threshold_dt)
    for run in orphans:
        await repo.update_run(
            session,
            run["id"],
            status=RunStatus.FAILED.value,
            stop_reason=StopReason.WORKER_LOST.value,
            error_code=ErrorCode.WORKER_LOST.value,
            error_message_safe="Worker perdido: ejecución sin latido reciente",
            finished_at=datetime.now(UTC),
        )
        await repo.insert_event(
            session,
            run["id"],
            level="error",
            event_type="worker_lost",
            stage=run["current_stage"],
            message_safe="Ejecución marcada como fallida por pérdida del worker",
            payload=None,
        )
    await session.commit()
    return len(orphans)
