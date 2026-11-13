"""Integración: API con servicios reales (DB + Redis simulado no necesario para
la mayoría de endpoints)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from bore.db.session import init_engine

from .conftest import DATABASE_URL

pytestmark = pytest.mark.requires_services


@pytest_asyncio.fixture
async def client(db):
    init_engine(DATABASE_URL)
    from bore.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health(client):
    r = await client.get("/health/live")
    assert r.status_code == 200


async def test_provider_not_configured(client, monkeypatch):
    r = await client.post("/api/v1/research-runs", json={"sector": "inmobiliario"})
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "provider_not_configured"


async def test_create_offline_run_and_cancel(client):
    r = await client.post("/api/v1/research-runs", json={"sector": "inmobiliario", "offline": True})
    # El encolado falla sin worker: la fila pasa a failed/enqueue_failed pero el
    # POST confirma antes la fila queued (202).
    assert r.status_code == 202
    run_id = r.json()["id"]
    detail = (await client.get(f"/api/v1/research-runs/{run_id}")).json()
    assert detail["status"] in ("queued", "failed")
    r2 = await client.post(f"/api/v1/research-runs/{run_id}/cancel")
    assert r2.status_code in (202, 409)
    # Una segunda creación inmediata incumple el intervalo o encuentra activa.
    r3 = await client.post("/api/v1/research-runs", json={"sector": "otro", "offline": True})
    assert r3.status_code in (202, 409)


async def test_404_problem_json(client):
    import uuid

    r = await client.get(f"/api/v1/research-runs/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "not_found"


async def test_validation_422(client):
    r = await client.post("/api/v1/research-runs", json={"requested_ideas": 99})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
