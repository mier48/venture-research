"""Integración: seguridad web (10.3/10.4) y exportaciones 8.4 (escape de
Markdown malicioso, orden y completitud)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from bore.api.security import csrf_token
from bore.config import get_settings
from bore.db import repositories as repo
from bore.db.session import init_engine
from bore.reporting.markdown import export_markdown

from .conftest import DATABASE_URL

pytestmark = pytest.mark.requires_services


@pytest_asyncio.fixture
async def client(db):
    init_engine(DATABASE_URL)
    from bore.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_forbidden_host(client):
    r = await client.get("/health/live", headers={"host": "evil.example.com"})
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden_host"


async def test_allowed_host_ok(client):
    r = await client.get("/health/live", headers={"host": "testserver"})
    assert r.status_code == 200


async def test_forbidden_origin_on_post(client):
    r = await client.post(
        "/api/v1/research-runs",
        json={"sector": "x", "offline": True},
        headers={"origin": "https://malicious.example.com"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden_origin"


async def test_allowed_origin_passes(client):
    origin = get_settings().allowed_origins[0]
    r = await client.post(
        "/api/v1/research-runs",
        json={"sector": "x", "offline": True},
        headers={"origin": origin},
    )
    assert r.status_code == 202


async def test_post_requires_json_content_type(client):
    r = await client.post(
        "/api/v1/research-runs",
        content=b"sector=x",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 415
    assert r.json()["code"] == "unsupported_media_type"


async def test_ui_post_requires_csrf(client):
    r = await client.post("/ui/runs", data={"sector": "x"})
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden_csrf"


async def test_ui_cancel_invalid_csrf_rejected(client):
    r = await client.post(
        "/ui/runs/00000000-0000-0000-0000-000000000000/cancel",
        data={"csrf": "invalido"},
    )
    assert r.status_code == 403


async def test_ui_cancel_valid_csrf_404_for_missing_run(client):
    token = csrf_token(get_settings())
    r = await client.post(
        "/ui/runs/00000000-0000-0000-0000-000000000000/cancel",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert r.status_code == 404


async def _costly_run(db, *, is_synthetic: bool, cost_usd: str) -> None:
    """Ejecución terminada ayer con llamadas de proveedor costeadas hoy."""
    from datetime import UTC, datetime, timedelta

    from .conftest import DEMO_CONFIG

    async with db() as s:
        run = await repo.create_run(
            s,
            sector="x",
            country="ES",
            language="es-ES",
            config=DEMO_CONFIG,
            is_synthetic=is_synthetic,
            model_config=None,
            prompt_version="t",
            rubric_version="r",
            pricing_version="p",
        )
        # Terminada y antigua para no activar las reglas de ejecución activa
        # ni de intervalo mínimo; solo interesa el coste diario.
        await repo.update_run(
            s,
            run["id"],
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=1),
        )
        await repo.insert_provider_call(
            s,
            research_run_id=run["id"],
            stage="demand_research",
            agent_role="demand_researcher",
            provider="openai" if not is_synthetic else "fake",
            cost_usd=cost_usd,
            outcome="ok",
        )
        await s.commit()


async def test_daily_budget_ignores_synthetic_runs(db, client):
    """Las ejecuciones sintéticas no consumen el límite diario (D35): coste
    estimado registrado, pero el POST de una nueva ejecución pasa (202)."""
    await _costly_run(db, is_synthetic=True, cost_usd="9999.00")
    r = await client.post(
        "/api/v1/research-runs",
        json={"sector": "x", "offline": True},
    )
    assert r.status_code == 202


async def test_daily_budget_blocks_real_runs(db, client):
    """Una ejecución real (is_synthetic=false) con coste superior al límite
    diario sí bloquea nuevas ejecuciones (409 daily_budget_exceeded)."""
    await _costly_run(db, is_synthetic=False, cost_usd="9999.00")
    r = await client.post(
        "/api/v1/research-runs",
        json={"sector": "x", "offline": True},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "daily_budget_exceeded"


async def test_export_escapes_malicious_markdown(db, run_id):
    """Una fuente con título/URL maliciosos no puede inyectar enlaces, imágenes
    ni HTML en el Markdown (8.4 + seguridad de render)."""
    async with db() as s:
        for _i, (title, url) in enumerate(
            [
                ("[x](javascript:alert(1))", "https://a.example.com/d1"),
                ("![img](https://evil.example.com/x.png)", "https://a.example.com/d2"),
                ("<script>alert(1)</script>", "https://a.example.com/d3"),
                ("título normal", "javascript:alert(1)"),
            ]
        ):
            await repo.upsert_source(
                s,
                research_run_id=run_id,
                url=url,
                canonical_url=url,
                registrable_domain="example.com",
                provenance="synthetic",
                title=title,
                access_status="ok",
            )
        await s.commit()
        run = await repo.get_run(s, run_id)
        md = await export_markdown(s, run)
    # Los títulos quedan escapados: no hay enlaces ni imágenes inyectables.
    assert "[x](javascript" not in md
    assert "\\[x\\]\\(javascript" in md
    assert "![img]" not in md
    assert "\\!\\[img\\]" in md
    assert "<script>" not in md
    # Una URL no-http no se enlaza: aparece como código, no como enlace.
    assert "](javascript:alert(1))" not in md
    assert "`javascript\\:alert\\(1\\)`" in md or "`javascript" in md


async def test_export_order_and_sections(db, run_id):
    async with db() as s:
        await repo.insert_opportunity(
            s,
            research_run_id=run_id,
            fingerprint="x",
            name="No publicada de ejemplo",
            sector_subcategory="s",
            customer_segment="seg",
            buyer="b",
            problem="p",
            solution="sol",
            value_proposition="v",
            business_model="m",
            status="insufficient_evidence",
            rejection_code="competition_unknown",
        )
        await s.commit()
        run = await repo.get_run(s, run_id)
        md = await export_markdown(s, run)
    [i for i, line in enumerate(md.splitlines()) if line.startswith(("##", "# "))]
    text = "\n".join(md.splitlines())
    idx_ranking = text.index("## Ranking")
    idx_fuentes = text.index("## Fuentes")
    idx_warning = text.index("Advertencia de alcance")
    assert idx_warning < idx_ranking < idx_fuentes
    assert "## Candidatas no publicadas" in md or "no publicadas" in md
    assert "DEMO" in md  # ejecución sintética
