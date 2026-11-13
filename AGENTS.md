# Reglas permanentes — Venture Research (BORE)

- Especificación obligatoria: `docs/SPEC.md`. Decisiones en `docs/DECISIONS.md` (fecha, decisión, motivo).
- NUNCA datos fabricados: las URL persistidas solo proceden de metadatos del proveedor de búsqueda
  (o `provenance=synthetic` en ejecuciones offline, marcadas `is_synthetic`). Sin clave API no se
  simula investigación real: el modo real sin `OPENAI_API_KEY` responde `provider_not_configured`.
- NUNCA secretos en el repositorio. Credenciales solo en `.env` (no versionado) o entorno.
- Los nombres de modelo vienen SIEMPRE de variables de entorno (`BORE_MODEL_*`); nunca literales.
- Gasto en APIs (sección 0.5 de la spec): llamadas de pago solo con `OPENAI_API_KEY` +
  `BORE_ALLOW_PAID_SMOKE=1`, una ejecución de humo con `limits.max_usd <= 1.0`. Lo demás, fakes.
- Calidad antes de dar algo por terminado (ejecutar de verdad):
  `uv sync --locked` · `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy` · `uv run pytest -q` · `docker compose config`
- Suite completa con servicios: `docker compose --profile test run --rm tests`.
- Demo offline: `docker compose run --rm api bore demo --offline`.
- El worker RQ solo corre dentro del contenedor Linux; nada obligatorio lo ejecuta en Windows.
- No afirmar en docs que los tests pasan si no se han ejecutado.
