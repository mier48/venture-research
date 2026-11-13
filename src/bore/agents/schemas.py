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
    rule: Literal[
        "not_software",
        "data_access",
        "sales_dependency",
        "llm_wrapper",
        "over_scope",
        "redundant",
    ]
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
    data_barriers: list[str] = Field(default_factory=list)
    legal_operational_risks: list[str] = Field(default_factory=list)
    llm_substitution_risk: str | None = None
    technical_scope_estimate: str | None = None
    maintenance_cost_estimate: str | None = None
    mvp_weeks_estimate: int | None = None
    price_hypothesis_eur_month: float | None = None
    variable_cost_eur_month: float | None = None
    fixed_cost_eur_month: float | None = None
    # Universo de compradores (nº estimado de clientes potenciales) e índice de
    # la afirmación en `claims` que lo documenta; la penetración solo se calcula
    # si esa afirmación queda `verified` (5.3).
    buyer_universe_customers: int | None = None
    buyer_universe_claim_index: int | None = None
    claims: list[ExtractedClaim] = Field(default_factory=list)


class EvidenceJudgeOutput(BaseModel):
    verdict: Literal["supports", "contradicts", "mentions", "not_found"]
    rationale: str = ""


class ClaimCitation(BaseModel):
    """Referencia del evaluador a una afirmación auditada (identificador C1…Cn
    asignado por el código en la entrada)."""

    ref: str
    role: Literal["evidence", "counterevidence"] = "evidence"


class CriterionScore(BaseModel):
    criterion: CriterionLiteral
    score: int | None = Field(default=None, ge=1, le=10)
    rationale: str = ""
    claims: list[ClaimCitation] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class CriterionAssessmentOutput(BaseModel):
    scores: list[CriterionScore]


class CriticalReviewOutput(BaseModel):
    objections: list[str]
    recommended_changes: list[str]
    final_verdict: Literal["keep_researching", "commercial_validation", "discard"]
    counter_claims: list[ExtractedClaim] = Field(default_factory=list)


# Papel -> esquema de salida estructurada que consume el pipeline (4.1).
# Obligatorio: un rol sin entrada aquí es un error de programación y debe
# fallar antes de llamar al proveedor.
ROLE_OUTPUT_TYPES: dict[str, type[BaseModel]] = {
    "sector_mapper": SectorMapOutput,
    "demand_researcher": DemandResearchOutput,
    "idea_generator": IdeaGenerationOutput,
    "dedup_screener": SemanticDedupOutput,
    "eligibility_assessor": EligibilityOutput,
    "screener": ScreeningOutput,
    "market_validation": MarketValidationOutput,
    "evidence_judge": EvidenceJudgeOutput,
    "criterion_assessor": CriterionAssessmentOutput,
    "critical_reviewer": CriticalReviewOutput,
}
