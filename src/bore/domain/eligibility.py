"""Decisión de elegibilidad en código a partir de las respuestas del
EligibilityAssessor (5.2)."""

from __future__ import annotations

from dataclasses import dataclass

from bore.domain.enums import EligibilityAnswer, OpportunityStatus, RejectionCode

RULES: tuple[RejectionCode, ...] = (
    RejectionCode.NOT_SOFTWARE,
    RejectionCode.DATA_ACCESS,
    RejectionCode.SALES_DEPENDENCY,
    RejectionCode.LLM_WRAPPER,
    RejectionCode.OVER_SCOPE,
    RejectionCode.REDUNDANT,
)


@dataclass(frozen=True)
class RuleAssessment:
    rule: RejectionCode
    answer: EligibilityAnswer
    reason: str


@dataclass(frozen=True)
class EligibilityDecision:
    status: OpportunityStatus  # eligible | rejected | needs_manual_review
    rejection_code: RejectionCode | None
    reason: str | None
    uncertainties: tuple[RejectionCode, ...]


# Más restrictiva gana cuando el evaluador repite una regla.
_SEVERITY: dict[EligibilityAnswer, int] = {
    EligibilityAnswer.NO: 0,
    EligibilityAnswer.UNCERTAIN: 1,
    EligibilityAnswer.YES: 2,
}

MISSING_RULE_REASON = "Sin respuesta del evaluador"


def consolidate(
    assessments: list[RuleAssessment],
) -> tuple[list[RuleAssessment], list[RejectionCode]]:
    """Consolida las respuestas del EligibilityAssessor por regla.

    - Regla duplicada → se conserva la respuesta más restrictiva
      (yes > uncertain > no).
    - Regla ausente → `uncertain` con motivo "Sin respuesta del evaluador";
      se devuelve además la lista de reglas faltantes para trazabilidad.
    """
    by_rule: dict[RejectionCode, RuleAssessment] = {}
    for a in assessments:
        prev = by_rule.get(a.rule)
        if prev is None or _SEVERITY[a.answer] > _SEVERITY[prev.answer]:
            by_rule[a.rule] = a
    missing = [rule for rule in RULES if rule not in by_rule]
    for rule in missing:
        by_rule[rule] = RuleAssessment(
            rule=rule,
            answer=EligibilityAnswer.UNCERTAIN,
            reason=MISSING_RULE_REASON,
        )
    return [by_rule[rule] for rule in RULES], missing


def decide(assessments: list[RuleAssessment]) -> EligibilityDecision:
    """- Algún `yes` → rejected con la primera regla afirmativa.
    - `uncertain` en data_access → needs_manual_review.
    - `uncertain` en cualquier otra regla → eligible con incertidumbre registrada."""
    for a in assessments:
        if a.answer is EligibilityAnswer.YES:
            return EligibilityDecision(
                status=OpportunityStatus.REJECTED,
                rejection_code=a.rule,
                reason=a.reason,
                uncertainties=(),
            )
    uncertainties = tuple(a.rule for a in assessments if a.answer is EligibilityAnswer.UNCERTAIN)
    if RejectionCode.DATA_ACCESS in uncertainties:
        reason = next(
            a.reason
            for a in assessments
            if a.rule is RejectionCode.DATA_ACCESS and a.answer is EligibilityAnswer.UNCERTAIN
        )
        return EligibilityDecision(
            status=OpportunityStatus.NEEDS_MANUAL_REVIEW,
            rejection_code=RejectionCode.DATA_ACCESS,
            reason=reason,
            uncertainties=uncertainties,
        )
    return EligibilityDecision(
        status=OpportunityStatus.ELIGIBLE,
        rejection_code=None,
        reason=None,
        uncertainties=uncertainties,
    )
