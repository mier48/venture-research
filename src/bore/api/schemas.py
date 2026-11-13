"""Esquemas Pydantic de la API (8.1)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class LimitsSchema(BaseModel):
    max_usd: Decimal = Decimal("10.0")
    max_web_search_calls: int = Field(default=180, ge=1, le=1000)
    max_model_calls: int = Field(default=200, ge=1, le=2000)
    max_duration_seconds: int = Field(default=5400, ge=60, le=14400)
    max_retries_per_stage: int = Field(default=2, ge=0, le=5)


class ResearchRunCreate(BaseModel):
    sector: str = Field(min_length=1, max_length=200)
    country: str = Field(default="ES", min_length=2, max_length=2)
    language: str = Field(default="es-ES", max_length=10)
    requested_ideas: int = Field(default=5, ge=1, le=10)
    candidate_pool_size: int = Field(default=25, ge=20, le=30)
    max_candidates_per_subsector: int = Field(default=5, ge=1, le=10)
    deep_dive_limit: int = Field(default=8, ge=3, le=12)
    review_limit: int = Field(default=6, ge=1, le=12)
    min_final_score: Decimal = Field(default=Decimal("7.0"))
    min_critical_criterion_score: int = Field(default=6, ge=1, le=10)
    min_any_criterion_score: int = Field(default=4, ge=1, le=10)
    max_mvp_weeks: int = Field(default=6, ge=1, le=52)
    max_initial_budget_eur: int = Field(default=500, ge=0, le=100000)
    min_target_operating_profit_eur_month: int = Field(default=500, ge=0, le=1000000)
    preferred_acquisition: list[str] = Field(
        default_factory=lambda: ["seo", "aso", "communities", "referrals"]
    )
    limits: LimitsSchema = Field(default_factory=LimitsSchema)
    offline: bool = False
    client_description: str | None = Field(default=None, max_length=2000)
    excluded_problems: list[str] | None = None
    known_competitors: list[str] | None = None
    blocked_terms: list[str] | None = None

    @field_validator("min_final_score", mode="before")
    @classmethod
    def _score_decimal(cls, v: Any) -> Decimal:
        # Nunca desde float: Decimal(str(valor)) (6.2).
        return Decimal(str(v))

    @model_validator(mode="after")
    def _check_limits(self) -> ResearchRunCreate:
        if not (Decimal("1") <= self.min_final_score <= Decimal("10")):
            raise ValueError("min_final_score debe estar entre 1 y 10")
        if self.review_limit < self.requested_ideas:
            raise ValueError("review_limit debe ser >= requested_ideas")
        if self.review_limit > self.deep_dive_limit:
            raise ValueError("review_limit debe ser <= deep_dive_limit")
        return self

    def to_config(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "country": self.country,
            "language": self.language,
            "requested_ideas": self.requested_ideas,
            "candidate_pool_size": self.candidate_pool_size,
            "max_candidates_per_subsector": self.max_candidates_per_subsector,
            "deep_dive_limit": self.deep_dive_limit,
            "review_limit": self.review_limit,
            "min_final_score": float(self.min_final_score),
            "min_critical_criterion_score": self.min_critical_criterion_score,
            "min_any_criterion_score": self.min_any_criterion_score,
            "max_mvp_weeks": self.max_mvp_weeks,
            "max_initial_budget_eur": self.max_initial_budget_eur,
            "min_target_operating_profit_eur_month": self.min_target_operating_profit_eur_month,
            "preferred_acquisition": self.preferred_acquisition,
            "limits": self.limits.model_dump(mode="json"),
        }


class ResearchRunCreated(BaseModel):
    id: str
    status: str
    current_stage: str | None
    progress_percent: int


class OpportunitySummary(BaseModel):
    id: str
    name: str
    status: str
    final_score: float | None = None
    confidence: str | None = None
    rank: int | None = None
    rejection_code: str | None = None


class RunDetail(BaseModel):
    id: str
    sector: str
    country: str
    status: str
    stop_reason: str | None
    current_stage: str | None
    progress_percent: int
    is_synthetic: bool
    error_code: str | None
    error_message_safe: str | None
    statistics: dict[str, int]
    cost: dict[str, Any]
    opportunities: list[OpportunitySummary]
    created_at: str
    finished_at: str | None
