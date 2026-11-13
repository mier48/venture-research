"""Rúbrica y función pura de scoring (sección 6 de la spec)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from bore.domain.enums import CriterionCode

RUBRIC_VERSION = "rubric-1.1"

WEIGHTS: dict[str, int] = {
    "demand": 25,
    "distribution": 20,
    "competition": 15,
    "monetization": 15,
    "technical": 10,
    "ai_defensibility": 10,
    "operational_legal": 5,
}
CRITICAL = frozenset({"demand", "distribution", "competition", "monetization"})

# Pesos del sondeo (etapa 5): los tres criterios preliminares renormalizados.
SCREENING_CRITERIA: tuple[str, ...] = ("demand", "distribution", "competition")


def weighted_score(scores: dict[str, int]) -> Decimal:
    if set(scores) != set(WEIGHTS):
        raise ValueError("Faltan o sobran criterios")
    if any(
        isinstance(value, bool) or type(value) is not int or not 1 <= value <= 10
        for value in scores.values()
    ):
        raise ValueError("Las notas deben ser enteros de 1 a 10")
    total = sum(Decimal(scores[key] * weight) for key, weight in WEIGHTS.items())
    return (total / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def passes_gates(
    scores: dict[str, int],
    *,
    min_final: Decimal,
    min_critical: int,
    min_any: int,
) -> bool:
    return (
        weighted_score(scores) >= min_final
        and all(scores[code] >= min_critical for code in CRITICAL)
        and all(value >= min_any for value in scores.values())
    )


def screening_score(scores: dict[str, int]) -> Decimal:
    """Media ponderada con los pesos de la rúbrica renormalizados sobre los tres
    criterios del sondeo (etapa 5). Interna: nunca se publica."""
    if set(scores) != set(SCREENING_CRITERIA):
        raise ValueError("El sondeo exige demand, distribution y competition")
    if any(
        isinstance(value, bool) or type(value) is not int or not 1 <= value <= 10
        for value in scores.values()
    ):
        raise ValueError("Las notas deben ser enteros de 1 a 10")
    total_weight = sum(WEIGHTS[code] for code in SCREENING_CRITERIA)
    total = sum(Decimal(scores[code] * WEIGHTS[code]) for code in SCREENING_CRITERIA)
    return (total / Decimal(total_weight)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def all_criteria() -> list[str]:
    return [c.value for c in CriterionCode]
