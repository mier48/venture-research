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
