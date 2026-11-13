"""Aplicación FastAPI: API REST, salud e interfaz mínima Jinja2+HTMX."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any

import redis as redis_lib
import sqlalchemy as sa
from fastapi import Depends, FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bore.api import routes_opportunities, routes_runs
from bore.api.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    validation_error_handler,
)
from bore.config import Settings, get_settings
from bore.db import repositories as repo
from bore.db.session import init_engine, new_session


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings = get_settings()
    init_engine(settings.database_url)
    yield


app = FastAPI(title="Venture Research — BORE", lifespan=lifespan)
app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, http_error_handler)

app.include_router(routes_runs.router, prefix="/api/v1", tags=["research-runs"])
app.include_router(routes_opportunities.router, prefix="/api/v1", tags=["opportunities"])

_static = resources.files("bore").joinpath("static")
app.mount("/static", StaticFiles(directory=str(_static)), name="static")
_templates = Jinja2Templates(directory=str(resources.files("bore").joinpath("templates")))
_templates.env.autoescape = True


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready(settings: Settings = Depends(get_settings)) -> Any:  # noqa: B008
    from fastapi.responses import JSONResponse

    try:
        async with new_session() as s:
            await s.execute(sa.select(1))
        r = redis_lib.Redis.from_url(settings.redis_url)
        r.ping()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}


# ------------------------------ Interfaz mínima -------------------------------


@app.get("/", response_class=HTMLResponse)
async def ui_index(request: Request) -> HTMLResponse:
    async with new_session() as s:
        runs = await repo.list_runs(s, status=None, limit=50, offset=0)
    return _templates.TemplateResponse(
        request, "index.html", {"runs": runs, "settings": get_settings()}
    )


@app.post("/ui/runs")
async def ui_create_run(
    sector: str = Form(...),
    offline: str | None = Form(default=None),
) -> RedirectResponse:
    import httpx

    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.bore_api_url, timeout=30) as client:
        resp = await client.post(
            "/api/v1/research-runs",
            json={"sector": sector, "offline": bool(offline)},
        )
    if resp.status_code == 202:
        run_id = resp.json()["id"]
        return RedirectResponse(f"/runs/{run_id}", status_code=303)
    return RedirectResponse("/?error=1", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def ui_run_detail(request: Request, run_id: uuid.UUID) -> HTMLResponse:
    async with new_session() as s:
        run = await repo.get_run(s, run_id)
        if run is None:
            raise ApiError(404, "not_found", "Ejecución no encontrada")
        opps = await repo.list_opportunities(s, run_id)
        stats = await repo.opportunity_status_counts(s, run_id)
    return _templates.TemplateResponse(
        request, "run.html", {"run": run, "opps": opps, "stats": stats}
    )


@app.get("/runs/{run_id}/progress", response_class=HTMLResponse)
async def ui_run_progress(request: Request, run_id: uuid.UUID) -> HTMLResponse:
    async with new_session() as s:
        run = await repo.get_run(s, run_id)
        if run is None:
            raise ApiError(404, "not_found", "Ejecución no encontrada")
        opps = await repo.list_opportunities(s, run_id)
        stats = await repo.opportunity_status_counts(s, run_id)
    return _templates.TemplateResponse(
        request, "_run_progress.html", {"run": run, "opps": opps, "stats": stats}
    )


@app.get("/opportunities/{opp_id}", response_class=HTMLResponse)
async def ui_opportunity(request: Request, opp_id: uuid.UUID) -> HTMLResponse:
    async with new_session() as s:
        opp = await repo.get_opportunity(s, opp_id)
        if opp is None:
            raise ApiError(404, "not_found", "Oportunidad no encontrada")
        run = await repo.get_run(s, opp["research_run_id"])
        comps = await repo.list_competitors(s, opp_id)
        claims = await repo.list_claims(s, opp["research_run_id"], opportunity_id=opp_id)
        scores_i = await repo.list_criterion_scores(s, opp_id, revision="initial")
        scores_f = await repo.list_criterion_scores(s, opp_id, revision="final")
        links = await repo.list_claim_sources(s, [c["id"] for c in claims])
        src_ids = {x["source_id"] for x in links}
        sources = [
            src
            for src in await repo.list_sources(s, opp["research_run_id"])
            if src["id"] in src_ids
        ]
    return _templates.TemplateResponse(
        request,
        "opportunity.html",
        {
            "opp": opp,
            "run": run,
            "competitors": comps,
            "claims": claims,
            "scores_i": {s["criterion_code"]: s for s in scores_i},
            "scores_f": {s["criterion_code"]: s for s in scores_f},
            "sources": sources,
        },
    )
