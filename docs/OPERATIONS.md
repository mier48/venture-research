# Operaciones

## Variables de entorno

| Variable | Defecto | Uso |
|---|---|---|
| `OPENAI_API_KEY` | *(vacío)* | Obligatoria en modo real |
| `BORE_MODEL_RESEARCH/ANALYSIS/REVIEW` | *(vacío)* | Obligatorios en modo real; validados con `models.retrieve` |
| `OPENAI_AGENTS_DISABLE_TRACING` | `1` | Trazas SDK desactivadas |
| `BORE_TRACING_ENABLED` | `0` | Activa trazas |
| `BORE_TRACE_INCLUDE_SENSITIVE_DATA` | `0` | Incluye datos sensibles en trazas |
| `BORE_SEARCH_CONTEXT_SIZE` | `medium` | Tamaño de contexto de WebSearchTool |
| `BORE_AGENT_MAX_TURNS` | `10` | max_turns del Runner |
| `BORE_ALLOW_PAID_SMOKE` | `0` | Habilita tests `integration_external` |
| `DATABASE_URL` | `postgresql+psycopg://bore:bore@postgres:5432/bore` | Conexión PG |
| `REDIS_URL` | `redis://redis:6379/0` | Cola RQ |
| `POSTGRES_USER/PASSWORD/DB` | `bore` | Contenedor postgres |
| `BORE_API_URL` | `http://127.0.0.1:8000` | Base URL del CLI |
| `BORE_SECRET_KEY` | *(vacío)* | Firma CSRF (vacío → aleatoria por proceso + warning) |
| `BORE_ALLOWED_HOSTS` | `127.0.0.1,localhost,api,testserver,test` | Host permitidos |
| `BORE_ALLOWED_ORIGINS` | `http://127.0.0.1:8000,http://localhost:8000` | Origin permitidos en POST |
| `BORE_MAX_USD_PER_DAY` | `25` | Tope diario UTC (suma `provider_calls.cost_usd`) |
| `BORE_MIN_SECONDS_BETWEEN_RUNS` | `60` | Intervalo mínimo entre POST |
| `BORE_HEARTBEAT_STALE_SECONDS` | `120` | running sin latido → failed/`worker_lost` |
| `BORE_REQUEUE_AFTER_SECONDS` | `300` | queued sin job RQ → reencolado una vez |
| `BORE_REAPER_INTERVAL_SECONDS` | `60` | Periodo del reaper del worker |
| `VERIFIER_USER_AGENT` | `BORE-SourceVerifier/1.0` | UA del verificador |
| `VERIFIER_TIMEOUT_SECONDS` | `15` | Timeout por petición |
| `VERIFIER_MAX_BYTES` | `2097152` | Tope de tamaño por streaming |
| `VERIFIER_MAX_REDIRECTS` | `5` | Saltos máximos |
| `VERIFIER_RESPECT_ROBOTS` | `1` | robots.txt con caché |
| `VERIFIER_PER_HOST_MIN_INTERVAL` | `1.0` | Mínimo entre peticiones al mismo host |
| `BORE_E2E_BASE_URL` | `http://127.0.0.1:8000` | Solo tests e2e |

Por ejecución (`limits` del POST): `max_usd`, `max_web_search_calls`,
`max_model_calls`, `max_duration_seconds`, `max_retries_per_stage`,
`search_split` (reparto 9.2) y `search_reserve` (24).

## Worker, latido y reaper

`bore worker` ejecuta trabajos RQ (`job_timeout = max_duration_seconds + 300`,
`result_ttl=0`). Al arrancar y cada ~60 s: `running` sin latido >
`BORE_HEARTBEAT_STALE_SECONDS` → `failed`/`worker_lost`; `queued` sin job RQ >
`BORE_REQUEUE_AFTER_SECONDS` → reencolado **una** vez (`run_requeued`).
Durante una ejecución el latido se actualiza ≤30 s y `cancel_requested_at` se
comprueba entre tareas y antes de cada llamada costosa.

## Presupuestos

Reparto por etapa de la tabla 9.2 + reserva de 24 búsquedas (una ampliación
por oportunidad). Comprobación previa (coste máximo estimado con
`pricing.toml` + `est_input_tokens` + `max_output_tokens`) contra USD restante,
cuotas y duración; registro posterior en `provider_calls`; exceso sobre lo
previsto bloquea la siguiente llamada. Corte → `partial` + `stop_reason`, sin
más llamadas, con ranking de las notas finales disponibles. Modelo sin tarifa
→ coste `fallback` marcado estimado (`cost_is_estimated`).

## Backups de PostgreSQL

```bash
docker compose exec postgres pg_dump -U bore bore > backup-$(date +%F).sql
docker compose exec -T postgres psql -U bore bore < backup.sql
```

## Borrado

`DELETE /api/v1/research-runs/{id}` (o botón de la UI), solo ejecuciones
terminales; borra en cascada oportunidades, fuentes, afirmaciones, notas,
revisiones, llamadas y eventos.

## Troubleshooting

- `provider_not_configured` (503): faltan `OPENAI_API_KEY`/`BORE_MODEL_*`.
- `rate_limited` / `run_already_active` (409): intervalo mínimo o ejecución
  activa; espera o cancela.
- `worker_lost`: el worker murió; la ejecución queda `failed` y puede relanzarse.
- Ejecución `queued` que no arranca: el reaper la reencola tras ~5 min;
  comprueba `docker compose logs worker`.
- `search_quota_exceeded`/`model_call_quota_exceeded`: ajusta `limits` del POST.
