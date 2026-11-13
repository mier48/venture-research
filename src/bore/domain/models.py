"""Modelos de dominio y validación de transiciones de estado (7.1)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bore.domain.enums import OPPORTUNITY_TRANSITIONS, OpportunityStatus


class IllegalTransitionError(ValueError):
    pass


def check_transition(current: OpportunityStatus, target: OpportunityStatus) -> None:
    if target not in OPPORTUNITY_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(f"Transición ilegal: {current} -> {target}")


@dataclass(frozen=True)
class Candidate:
    """Candidata generada por IdeaGenerator (antes de persistir)."""

    name: str
    sector_subcategory: str
    customer_segment: str
    buyer: str
    problem: str
    solution: str
    value_proposition: str
    business_model: str
    mvp_scope: list[str]
    excluded_features: list[str]
    acquisition: dict[str, Any]
    assumptions: dict[str, Any]


@dataclass(frozen=True)
class ScoringConfig:
    """Umbrales y límites efectivos de una ejecución (5.1)."""

    requested_ideas: int
    candidate_pool_size: int
    max_candidates_per_subsector: int
    deep_dive_limit: int
    review_limit: int
    min_final_score: Decimal
    min_critical_criterion_score: int
    min_any_criterion_score: int
    max_mvp_weeks: int
    max_initial_budget_eur: int
    min_target_operating_profit_eur_month: int
    max_usd: Decimal
    max_web_search_calls: int
    max_model_calls: int
    max_duration_seconds: int
    max_retries_per_stage: int
