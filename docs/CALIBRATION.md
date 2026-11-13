# Calibración de la rúbrica (6.5)

`tests/fixtures/calibration/` contiene 8 oportunidades etiquetadas a mano
(4 `weak`, 4 `good`) con notas de referencia por criterio; los datos son
ficticios (dominios `example.*`).

## Test offline

    uv run pytest tests/unit/test_calibration.py -v

Comprueba que `weighted_score` (+ `passes_gates` + confianza, vía el orden de
la rúbrica) coloca **todas** las buenas por encima de las débiles. Si el orden
falla, la rúbrica o los anclajes (docs/RUBRIC.md) deben revisarse.

## Evaluador real (opcional, consume llamadas de pago)

    OPENAI_API_KEY=... BORE_ALLOW_PAID_SMOKE=1 BORE_MODEL_ANALYSIS=... \
      uv run pytest -m integration_external tests/unit/test_calibration.py

Ejecuta el CriterionAssessor real dos veces sobre los fixtures y mide orden y
estabilidad (≤1 punto de diferencia en ≥80 % de los criterios). **El objetivo
de estabilidad es una estimación**, no una garantía: los modelos no son
deterministas.
