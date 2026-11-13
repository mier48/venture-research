"""Esquemas Pydantic de salida de cada papel de agente (4.1, 4.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CriterionLiteral = Literal[
    "demand",
    "distribution",
    "competition",
    "monetization",
    "technical",
    "ai_defensibility",
    "operational_legal",
]


class ExtractedClaim(BaseModel):
    """Contrato de estructuración de afirmaciones (4.2)."""

    text: str
    kind: Literal["fact", "estimate", "hypothesis"]
    criterion: CriterionLiteral | None
    source_refs: list[str] = Field(default_factory=list)
    evidence_quote: str | None = Field(default=None, max_length=300)


class SectorMapOutput(BaseModel):
    subsectors: list[str]
    actors: list[str]
    processes: list[str]
    problems: list[str]
    buyer_universe_note: str | None = None
    search_terms: list[str]
    claims: list[ExtractedClaim]


class DemandResearchOutput(BaseModel):
    demand_signals: list[str]
    information_gaps: list[str]
    claims: list[ExtractedClaim]


class GeneratedIdea(BaseModel):
    name: str
    sector_subcategory: str
    customer_segment: str
    buyer: str
    problem: str
    solution: str
    value_proposition: str
    business_model: str
    mvp_scope: list[str] = Field(default_factory=list)
    excluded_features: list[str] = Field(default_factory=list)
    acquisition_channels: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class IdeaGenerationOutput(BaseModel):
    ideas: list[GeneratedIdea]


class SemanticDedupOutput(BaseModel):
    """Grupos de candidatas equivalentes: índices de la lista recibida."""

    groups: list[list[int]] = Field(default_factory=list)


class RuleAnswer(BaseModel):
    rule: str
    answer: Literal["yes", "no", "uncertain"]
    reason: str


class EligibilityOutput(BaseModel):
    candidate_index: int
    assessments: list[RuleAnswer]


class ScreeningOutput(BaseModel):
    candidate_index: int
    demand: int = Field(ge=1, le=10)
    distribution: int = Field(ge=1, le=10)
    competition: int = Field(ge=1, le=10)
    notes: str = ""
    claims: list[ExtractedClaim] = Field(default_factory=list)


class CompetitorInfo(BaseModel):
    name: str
    website_url: str | None = None
    category: Literal["direct", "indirect", "substitute"]
    positioning: str = ""
    price_value: float | None = None
    price_currency: str | None = None
    price_unit: str | None = None
    price_country: str | None = None
    price_source_ref: str | None = None
    # Índice de la afirmación (en claims de la misma salida) que documenta el
    # precio; el código lo resuelve a price_claim_id tras la auditoría.
    price_claim_index: int | None = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class MarketValidationOutput(BaseModel):
    competitors: list[CompetitorInfo] = Field(default_factory=list)
    acquisition: list[str] = Field(default_factory=list)
    technical_notes: list[str] = Field(default_factory=list)
    mvp_weeks_estimate: int | None = None
    price_hypothesis_eur_month: float | None = None
    variable_cost_eur_month: float | None = None
    fixed_cost_eur_month: float | None = None
    claims: list[ExtractedClaim] = Field(default_factory=list)


class EvidenceJudgeOutput(BaseModel):
    verdict: Literal["supports", "contradicts", "mentions", "not_found"]
    rationale: str = ""


class CriterionScore(BaseModel):
    criterion: CriterionLiteral
    score: int | None = Field(default=None, ge=1, le=10)
    rationale: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class CriterionAssessmentOutput(BaseModel):
    scores: list[CriterionScore]


class CriticalReviewOutput(BaseModel):
    objections: list[str]
    recommended_changes: list[str]
    final_verdict: Literal["keep_researching", "commercial_validation", "discard"]
    counter_claims: list[ExtractedClaim] = Field(default_factory=list)
