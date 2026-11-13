# Arquitectura

```
 ┌────────┐   HTTP    ┌──────────────────┐     enqueue     ┌─────────┐
 │ UI/CLI │──────────▶│ FastAPI (api)    │───────────────▶ │  Redis  │
 └────────┘           │  /api/v1 + Jinja │                  │   RQ    │
                      │  security layer  │                  └────┬────┘
                      └───────┬──────────┘                       │
                              │ SQLAlchemy async                 │ job
                              ▼                                 ▼
                       ┌─────────────┐                  ┌─────────────┐
                       │ PostgreSQL  │◀─────────────────│   worker    │
                       │  (verdad)   │                  │  pipeline   │
                       └─────────────┘                  └──────┬──────┘
                                                              │ contratos
                                            ┌─────────────────┼───────────────┐
                                            ▼                 ▼               ▼
                                     ResearchProvider  AnalysisProvider  SourceVerifier
                                     (fakes | OpenAI)  (fakes | OpenAI) (fake | HTTP+SSRF)
```

## Capas

- **`domain/`**: lógica pura — enums de estado, scoring (6.2), confianza (6.3),
  evidencia (canonical_url, dominio registrable, quote_match, tabla 4.4),
  elegibilidad (5.2), economía unitaria (5.3), deduplicación (4.5),
  transiciones de oportunidad. Sin I/O.
- **`providers/`**: contratos (`ResearchProvider`, `AnalysisProvider`,
  `SourceVerifier`, `UsageRecord`), fakes deterministas y adaptadores OpenAI
  (Agents SDK 0.24.0) + `HttpSourceVerifier` anti-SSRF. La orquestación solo
  depende de los contratos.
- **`agents/`**: prompts versionados por papel, esquemas Pydantic de salida,
  fábrica de agentes del SDK.
- **`orchestration/`**: pipeline de 10 etapas, stage_runs idempotentes
  (input_hash), presupuestos con reparto por etapa, reintentos transitorios,
  cancelación cooperativa, latido, reaper y reencolado.
- **`db/`**: SQLAlchemy Core + Alembic; inserciones idempotentes ON CONFLICT.
- **`api/`**: FastAPI problem+json, middleware de seguridad
  (Host/Origin/Content-Type/CSRF), UI Jinja2+HTMX.
- **`reporting/`**: exportaciones Markdown/JSON (8.4).

## Las 10 etapas

1. `sector_mapping` → 2. `demand_research` → 3. `candidate_generation`
   (caps en código + dedup semántico) → 4. `eligibility_filter` →
   5. `shallow_screening` (sondeo, reparto 9.2) → 6. `market_validation`
   (competidores + economía + reserva de ampliación) → 7. `evidence_audit`
   (verificación + price_status + penetración) → 8. `scoring` (initial) →
   9. `adversarial_review` (contraevidencia + final + `score_changed`) →
   10. `ranking_and_report` (puertas, desempate, invariante, estadísticas).

Cada etapa cuesta: investigar (opcional) → estructurar (salida tipada) →
validar en código (refs desconocidas descartadas, fact sin refs → hipótesis,
URLs solo del proveedor). Patrón de semáforo 3 por oportunidad.
