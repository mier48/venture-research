# Venture Research (BORE)

Sistema privado y auditable de investigación de oportunidades de negocio
software/SaaS/web/móvil: dado un sector (p. ej. «inmobiliario»), genera
candidatas, las depuplica y filtra, valida evidencia (investigar → estructurar
→ auditar), puntúa con una rúbrica ponderada, revisa de forma adversaria y
publica solo las que superan todas las puertas.

> **Advertencia de alcance**: la nota prioriza líneas de investigación; no
> demuestra rentabilidad ni disposición real a pagar. Toda afirmación muestra
> su estado de verificación y las fuentes que la respaldan o contradicen.

## Requisitos

- Windows con Docker Desktop (engine Linux/WSL2) o Linux con Docker + Compose v2.
- Para desarrollo local: uv 0.10+ y Python 3.13 (uv lo descarga si falta).

## Arranque

```bash
cp .env.example .env
docker compose up --build -d
# API + UI: http://127.0.0.1:8000  (PostgreSQL y Redis no publican puertos)
```

## Demo offline (sin claves, sin llamadas de pago)

```bash
docker compose run --rm api bore demo --offline
```

Genera una investigación sintética completa (~25 candidatas, algunas
publicadas). Toda ejecución sintética está marcada `is_synthetic` y muestra el
aviso «DEMO — datos ficticios» en UI y exportaciones. Los datos ficticios
**nunca** se presentan como investigación real.

## Investigación real

Configura en `.env`:

```bash
OPENAI_API_KEY=sk-...
BORE_MODEL_RESEARCH=gpt-...   # con búsqueda web hospedada
BORE_MODEL_ANALYSIS=gpt-...
BORE_MODEL_REVIEW=gpt-...
```

Luego, desde la UI (http://127.0.0.1:8000) desmarca «Demo offline», o por CLI:

```bash
bore research "inmobiliario" --country ES
bore status <run_id>
bore export <run_id> --format md|json
```

Sin `OPENAI_API_KEY` o sin modelos, `offline=false` devuelve 503
`provider_not_configured`: nunca se finge investigación.

**Datos enviados a proveedores externos**: solo el sector/país/idioma, los
resúmenes de candidatas y el texto de las páginas citadas (para verificación
de citas). Las trazas del SDK están desactivadas por defecto
(`OPENAI_AGENTS_DISABLE_TRACING=1`); se activan con `BORE_TRACING_ENABLED=1`
y `BORE_TRACE_INCLUDE_SENSITIVE_DATA=1` incluye entradas/salidas completas.

## Exportar

`GET /api/v1/research-runs/{id}/export?format=md|json` (también desde la UI):
cabecera con versiones y coste, advertencia de alcance, ranking, fichas
completas, no publicadas con motivo y fuentes numeradas.

## Límites y seguridad

- Una ejecución activa a la vez + intervalo mínimo entre POST + tope USD/día.
- Presupuestos por ejecución (USD, llamadas al modelo, búsquedas, duración)
  con reparto por etapa y corte a `partial`.
- Verificador HTTP anti-SSRF, robots.txt, ≤1 req/s/host, sin rastreador.
- Validación de Host/Origin, Content-Type JSON en POST y CSRF en formularios.

## Desarrollo / calidad (13.4)

```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q                                   # unitarios (host)
docker compose --profile test run --rm tests       # suite completa en compose
uv run playwright install chromium && uv run pytest -m e2e   # E2E contra el stack
```

Tests externos de pago (desactivados por defecto): ver docs/DEVELOPMENT.md.

## Documentación

- `docs/SPEC.md` — especificación completa (fuente de verdad).
- `docs/ARCHITECTURE.md`, `docs/RUBRIC.md`, `docs/DATA_PROVENANCE.md`,
  `docs/OPERATIONS.md`, `docs/CALIBRATION.md`, `docs/DEVELOPMENT.md`,
  `docs/DECISIONS.md`.

## Limitaciones conocidas

- Compatibilidad del modelo con web search no se garantiza hasta la primera
  llamada (el fallo se traduce a `provider_error`).
- Riesgo residual de DNS rebinding en el verificador (documentado en
  DECISIONS D18).
- El verificador real no se usa en ejecuciones offline.
