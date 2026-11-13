# Rúbrica de evaluación (sección 6)

`rubric_version = "rubric-1.1"` (persistida en cada ejecución y en
`criterion_scores`).

## Criterios y pesos (6.2)

| Criterio | Peso | Crítico |
|---|---:|---|
| demand | 25 | sí |
| distribution | 20 | sí |
| competition | 15 | sí |
| monetization | 15 | sí |
| technical | 10 | no |
| ai_defensibility | 10 | no |
| operational_legal | 5 | no |

Nota final = media ponderada exacta (`Decimal`, 2 decimales, ROUND_HALF_UP) de
los 7 criterios; faltar cualquier criterio → sin nota (insufficient_evidence).

## Puertas de publicación (6.4)

- Todas las notas 1–10 presentes.
- `final_score >= min_final_score` (config).
- Todo criterio crítico `>= min_critical_criterion_score`.
- Todo criterio `>= min_any_criterion_score`.
- ≥3 fuentes externas distintas vinculadas a afirmaciones de la candidata.
- ≥2 dominios registrables independientes **solo** entre fuentes de
  afirmaciones de criterios críticos.
- Evidencia de competencia (nunca «sin competencia»).
- Sin contradicción central sin resolver (afirmación `contradicted` en
  criterio crítico → insufficient_evidence).
- Revisión adversaria completada (solo se publican revisadas con nota final).

Desempate: nota ↓, confianza global ↓, demand ↓, generated_at ↑.
Las cualificadas que exceden `requested_ideas` quedan `scored`
(`qualified_not_published`); nunca se rellena el cupo.

## Anclajes de nota (guía del evaluador)

1–3 débil: sin evidencia de demanda, distribución opaca o competencia feroz
sin ventaja. 4–6 medio: señales parciales o con vacíos reconocidos.
7–8 buena: evidencia consistente de varias fuentes. 9–10 excelente: evidencia
fuerte, diversa y reciente en los criterios críticos.

## Confianza (6.3)

`high/medium/low` por criterio según base de evidencia mínima
(`has_min_evidence_base`) y estados de afirmación; la global se combina por
tablas de `confidence.py`. **La confianza nunca altera la fórmula de la
nota**: solo desempata y se muestra en la ficha.

## Revisión adversaria

`initial` → revisión (objeciones, cambios recomendados, veredicto
keep_researching/commercial_validation/discard) → `final`. El diff por
criterio se emite como evento `score_changed` y se expone en la ficha
(`criterion_scores.diff`).

## Política de versionado

Cambio en pesos/anclajes/puertas → nueva `rubric_version`; ejecuciones
antiguas conservan la suya (no se recalcula retroactivamente).
