"""CLI BORE (Typer): cliente HTTP de la API + demo offline en proceso."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import typer

from bore.config import get_settings

app = typer.Typer(help="Venture Research — motor BORE")

_TERMINAL = {"completed", "partial", "failed", "cancelled"}


def _api() -> str:
    return get_settings().bore_api_url.rstrip("/")


@app.command()
def research(
    sector: str,
    country: str = "ES",
    ideas: int = 5,
    min_score: float = 7.0,
    offline: bool = False,
    output: Path | None = None,
) -> None:
    """Crea una investigación vía API y sigue el progreso hasta el final."""
    base = _api()
    with httpx.Client(base_url=base, timeout=30) as client:
        resp = client.post(
            "/api/v1/research-runs",
            json={
                "sector": sector,
                "country": country,
                "requested_ideas": ideas,
                "min_final_score": min_score,
                "offline": offline,
            },
        )
        if resp.status_code != 202:
            typer.echo(f"Error {resp.status_code}: {resp.text}", err=True)
            raise typer.Exit(1)
        run_id = resp.json()["id"]
        typer.echo(f"Ejecución creada: {run_id}")
        while True:
            d = client.get(f"/api/v1/research-runs/{run_id}").json()
            typer.echo(f"  {d['status']} · {d['current_stage'] or '-'} · {d['progress_percent']}%")
            if d["status"] in _TERMINAL:
                break
            time.sleep(3)
        md = client.get(f"/api/v1/research-runs/{run_id}/export", params={"format": "md"})
        if output:
            output.write_text(md.text, encoding="utf-8")
            typer.echo(f"Informe guardado en {output}")
        else:
            typer.echo(md.text)


@app.command()
def status(run_id: str) -> None:
    """Estado de una ejecución (cliente de la API)."""
    with httpx.Client(base_url=_api(), timeout=30) as client:
        resp = client.get(f"/api/v1/research-runs/{run_id}")
        typer.echo(resp.text)


@app.command()
def export(run_id: str, format: str = "md", output: Path | None = None) -> None:
    """Exporta el informe de una ejecución (cliente de la API)."""
    with httpx.Client(base_url=_api(), timeout=30) as client:
        resp = client.get(f"/api/v1/research-runs/{run_id}/export", params={"format": format})
        if resp.status_code != 200:
            typer.echo(f"Error {resp.status_code}: {resp.text}", err=True)
            raise typer.Exit(1)
        if output:
            output.write_text(resp.text, encoding="utf-8")
            typer.echo(f"Guardado en {output}")
        else:
            typer.echo(resp.text)


@app.command()
def demo(offline: bool = typer.Option(True, "--offline")) -> None:
    """Ejecuta el pipeline completo en proceso, sin cola, con proveedores fake."""
    if not offline:
        typer.echo("La demo solo admite --offline", err=True)
        raise typer.Exit(1)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_demo())


async def _demo() -> None:
    from bore.agents.prompt_registry import composite_prompt_version, prompt_versions
    from bore.db import repositories as repo
    from bore.db.session import init_engine, new_session
    from bore.domain.scoring import RUBRIC_VERSION
    from bore.orchestration.budgets import load_pricing
    from bore.orchestration.pipeline import run_pipeline
    from bore.providers.fakes import make_fake_providers

    settings = get_settings()
    init_engine(settings.database_url)
    research, analysis, verifier = make_fake_providers()
    config: dict[str, Any] = {
        "sector": "inmobiliario",
        "country": "ES",
        "language": "es-ES",
        "requested_ideas": 5,
        "candidate_pool_size": 25,
        "max_candidates_per_subsector": 5,
        "deep_dive_limit": 8,
        "review_limit": 6,
        "min_final_score": 7.0,
        "min_critical_criterion_score": 6,
        "min_any_criterion_score": 4,
        "max_mvp_weeks": 6,
        "max_initial_budget_eur": 500,
        "min_target_operating_profit_eur_month": 500,
        "preferred_acquisition": ["seo", "aso", "communities", "referrals"],
        "limits": {
            "max_usd": 10.0,
            "max_web_search_calls": 180,
            "max_model_calls": 200,
            "max_duration_seconds": 5400,
            "max_retries_per_stage": 2,
        },
    }
    async with new_session() as session:
        run = await repo.create_run(
            session,
            sector=config["sector"],
            country=config["country"],
            language=config["language"],
            config=config,
            is_synthetic=True,
            model_config={
                "research": None,
                "analysis": None,
                "review": None,
                "prompt_versions": prompt_versions(),
            },
            prompt_version=composite_prompt_version(),
            rubric_version=RUBRIC_VERSION,
            pricing_version=load_pricing().pricing_version,
        )
        await session.commit()
        run_id = run["id"]
        typer.echo(f"Demo offline: ejecución {run_id}")
    from bore.db import session as db_session

    async with new_session() as session:
        await run_pipeline(
            session,
            db_session._sessionmaker,
            run_id,
            config,
            is_synthetic=True,
            research=research,
            analysis=analysis,
            verifier=verifier,
        )
    async with new_session() as session:
        final = await repo.get_run(session, run_id)
        assert final is not None
        stats = await repo.opportunity_status_counts(session, run_id)
    typer.echo(f"Terminada: {final['status']} ({final['stop_reason'] or 'sin stop_reason'})")
    typer.echo(f"Estadísticas: {stats}")
    typer.echo(f"Nota: ejecución sintética (is_synthetic=true). ID: {run_id}")


@app.command()
def worker() -> None:
    """Arranca el worker RQ (solo en el contenedor Linux): comprobación de modo
    real, mantenimiento de huérfanas y reaper periódico (9.4)."""
    import threading

    from bore.observability import setup_logging
    from bore.orchestration import jobs
    from bore.providers.openai_research import check_models_exist, missing_real_mode_config

    setup_logging()
    settings = get_settings()
    if settings.openai_api_key:
        missing = missing_real_mode_config(settings)
        if missing:
            typer.echo("Modo real mal configurado: " + "; ".join(missing), err=True)
            raise typer.Exit(2)
        problems = check_models_exist(settings)
        if problems:
            typer.echo(
                "Modelos no disponibles en el proveedor: " + "; ".join(problems),
                err=True,
            )
            raise typer.Exit(2)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_startup())
    stop = threading.Event()
    reaper = threading.Thread(target=jobs.reaper_loop, args=(stop,), daemon=True)
    reaper.start()
    import redis as redis_lib
    from rq import Worker

    conn = redis_lib.Redis.from_url(settings.redis_url)
    w = Worker(["bore"], connection=conn)
    typer.echo("Worker RQ en marcha (cola 'bore')")
    try:
        w.work(with_scheduler=False)
    finally:
        stop.set()


async def _startup() -> None:
    from bore.orchestration.jobs import startup_maintenance

    await startup_maintenance()
