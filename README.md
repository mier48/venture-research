# Venture Research (BORE)

Motor privado de investigación sistemática de oportunidades de negocio (software/SaaS).
Especificación completa: `docs/SPEC.md`. Decisiones: `docs/DECISIONS.md`.

## Arranque (Docker Desktop)

```bash
cp .env.example .env
docker compose up --build -d
curl http://127.0.0.1:8000/health/ready
```

UI: http://127.0.0.1:8000 · API: `/api/v1/...` (OpenAPI en `/docs`).

## Demo sin clave API (datos ficticios)

```bash
docker compose run --rm api bore demo --offline
```

Produce una ejecución persistida con `is_synthetic=true` y aviso «DEMO — datos ficticios».

## Modo real

Configura `OPENAI_API_KEY` y `BORE_MODEL_RESEARCH` / `BORE_MODEL_ANALYSIS` /
`BORE_MODEL_REVIEW` en `.env` y crea la ejecución con `offline=false`.
Sin clave, el modo real responde `provider_not_configured` (503): el sistema
nunca finge haber investigado Internet.

## CLI (cliente de la API)

```bash
uv run bore research --sector inmobiliario --offline --output informe.md
uv run bore status <run-id>
uv run bore export <run-id> --format json --output resultado.json
uv run bore demo --offline
```

## Datos enviados a proveedores externos (modo real)

Se envían a OpenAI: el sector y mercado configurados, las consultas de búsqueda,
los textos de investigación y las afirmaciones estructuradas. Las trazas del
Agents SDK están **desactivadas por defecto** (`OPENAI_AGENTS_DISABLE_TRACING=1`);
para activarlas: `BORE_TRACING_ENABLED=1` y, opcionalmente,
`BORE_TRACE_INCLUDE_SENSITIVE_DATA=1`. No están disponibles con Zero Data Retention.

## Tests

```bash
uv run pytest -q                              # unitarios
docker compose --profile test run --rm tests  # suite completa con servicios
```

## Estado del proyecto

Entrega Fase 0+1 con pipeline offline completo (proveedores simulados).
Adaptadores OpenAI reales, verificador HTTP con SSRF, exportación completa 8.4
y pruebas E2E llegan en fases siguientes.
