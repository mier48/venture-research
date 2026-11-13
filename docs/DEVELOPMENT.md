# Desarrollo — Venture Research (BORE)

## Versiones resueltas (uv.lock, 2026-11-13)

| Dependencia | Versión |
|---|---|
| Python | 3.13 (.python-version), imagen `python:3.13.18-slim-bookworm` |
| fastapi | 0.162.5 |
| uvicorn | 0.54.0 |
| pydantic | 2.14.3 |
| pydantic-settings | 2.16.2 |
| sqlalchemy[asyncio] | 2.1.8 |
| psycopg[binary] | 3.3.9 |
| alembic | 1.20.1 |
| rq | 3.0.0 |
| redis | 8.1.0 |
| jinja2 | 3.1.6 |
| python-multipart | 0.0.33 |
| typer | 0.29.1 |
| httpx | 0.28.1 |
| tldextract | 5.4.0 |
| openai-agents | 0.24.0 (última 0.2x; adaptador real en Fase 2) |
| pytest | 9.2.0 · pytest-asyncio 1.5.0 · respx 0.23.1 |
| ruff | 0.17.5 · mypy 2.4.1 · pre-commit 4.7.0 |
| playwright / pytest-playwright | 1.65.1 / 0.10.0 (Fase 4) |
| PostgreSQL | `postgres:18-alpine3.24` · Redis `redis:8.8.4-alpine` |
| uv | 0.10.7 |

## Vendorizado

- `src/bore/static/htmx.min.js` — HTMX 2.0.8 (descargado de jsDelivr el 2026-11-13). Sin CDN en ejecución.

## Comandos (13.4)

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q                      # unitarios; salta requires_services
docker compose config
docker compose up --build -d
docker compose run --rm api bore demo --offline
docker compose --profile test run --rm tests   # suite completa en compose
```

## Tests con servicios en el host

```bash
BORE_TEST_DATABASE_URL=postgresql+psycopg://bore:bore@127.0.0.1:55432/bore_test \
BORE_TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest -q tests/integration
```

## Notas

- psycopg async no admite el ProactorEventLoop de Windows: `jobs.asyncio_run` y la CLI fijan `WindowsSelectorEventLoopPolicy` en win32.
- El worker RQ solo corre en el contenedor (`bore worker`): marca huérfanas antes de `rq.Worker`.
- `config/pricing.toml`: solo el precio real de búsqueda web (10 USD/1.000); modelos ausentes → coste estimado con tarifa `fallback` etiquetada.
