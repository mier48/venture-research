"""Enumeraciones del dominio BORE (secciones 4.4, 5.2, 7.1, 9.1 de la spec)."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    BUDGET_EXCEEDED = "budget_exceeded"
    TIME_EXCEEDED = "time_exceeded"
    SEARCH_QUOTA_EXCEEDED = "search_quota_exceeded"
    MODEL_CALL_QUOTA_EXCEEDED = "model_call_quota_exceeded"
    CANCELLED_BY_USER = "cancelled_by_user"
    PROVIDER_ERROR = "provider_error"
    WORKER_LOST = "worker_lost"


class ErrorCode(StrEnum):
    PROVIDER_AUTH = "provider_auth"
    PROVIDER_ERROR = "provider_error"
    RATE_LIMITED = "rate_limited"
    CONTRACT_VIOLATION = "contract_violation"
    ENQUEUE_FAILED = "enqueue_failed"
    WORKER_LOST = "worker_lost"
    INTERNAL_ERROR = "internal_error"


class StageId(StrEnum):
    SECTOR_MAPPING = "sector_mapping"
    DEMAND_RESEARCH = "demand_research"
    CANDIDATE_GENERATION = "candidate_generation"
    ELIGIBILITY_FILTER = "eligibility_filter"
    SHALLOW_SCREENING = "shallow_screening"
    MARKET_VALIDATION = "market_validation"
    EVIDENCE_AUDIT = "evidence_audit"
    SCORING = "scoring"
    ADVERSARIAL_REVIEW = "adversarial_review"
    RANKING_AND_REPORT = "ranking_and_report"


STAGE_ORDER: tuple[StageId, ...] = (
    StageId.SECTOR_MAPPING,
    StageId.DEMAND_RESEARCH,
    StageId.CANDIDATE_GENERATION,
    StageId.ELIGIBILITY_FILTER,
    StageId.SHALLOW_SCREENING,
    StageId.MARKET_VALIDATION,
    StageId.EVIDENCE_AUDIT,
    StageId.SCORING,
    StageId.ADVERSARIAL_REVIEW,
    StageId.RANKING_AND_REPORT,
)

# Pesos fijos de progreso por etapa (9.1), índice = posición en STAGE_ORDER.
STAGE_PROGRESS_WEIGHTS: tuple[int, ...] = (5, 10, 5, 2, 13, 35, 10, 5, 12, 3)


class OpportunityStatus(StrEnum):
    CANDIDATE = "candidate"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    ELIGIBLE = "eligible"
    SHORTLISTED = "shortlisted"
    NOT_SHORTLISTED = "not_shortlisted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SCORED = "scored"
    PUBLISHED = "published"
    BELOW_THRESHOLD = "below_threshold"
    NOT_REVIEWED = "not_reviewed"


# Transiciones permitidas (7.1).
OPPORTUNITY_TRANSITIONS: dict[OpportunityStatus, frozenset[OpportunityStatus]] = {
    OpportunityStatus.CANDIDATE: frozenset(
        {
            OpportunityStatus.DUPLICATE,
            OpportunityStatus.REJECTED,
            OpportunityStatus.NEEDS_MANUAL_REVIEW,
            OpportunityStatus.ELIGIBLE,
        }
    ),
    OpportunityStatus.ELIGIBLE: frozenset(
        {OpportunityStatus.SHORTLISTED, OpportunityStatus.NOT_SHORTLISTED}
    ),
    OpportunityStatus.SHORTLISTED: frozenset(
        {OpportunityStatus.INSUFFICIENT_EVIDENCE, OpportunityStatus.SCORED}
    ),
    OpportunityStatus.SCORED: frozenset(
        {
            OpportunityStatus.PUBLISHED,
            OpportunityStatus.BELOW_THRESHOLD,
            OpportunityStatus.INSUFFICIENT_EVIDENCE,
            OpportunityStatus.NOT_REVIEWED,
        }
    ),
}


class RejectionCode(StrEnum):
    NOT_SOFTWARE = "not_software"
    DATA_ACCESS = "data_access"
    SALES_DEPENDENCY = "sales_dependency"
    LLM_WRAPPER = "llm_wrapper"
    OVER_SCOPE = "over_scope"
    REDUNDANT = "redundant"
    COMPETITION_UNKNOWN = "competition_unknown"


class EligibilityAnswer(StrEnum):
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


class Provenance(StrEnum):
    PROVIDER_ANNOTATION = "provider_annotation"
    PROVIDER_SOURCE_LIST = "provider_source_list"
    USER_SUPPLIED = "user_supplied"
    SYNTHETIC = "synthetic"


class AccessStatus(StrEnum):
    OK = "ok"
    REDIRECTED = "redirected"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    TIMEOUT = "timeout"
    ERROR = "error"
    NOT_CHECKED = "not_checked"


class ClaimKind(StrEnum):
    FACT = "fact"
    ESTIMATE = "estimate"
    HYPOTHESIS = "hypothesis"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


class JudgeVerdict(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MENTIONS = "mentions"
    NOT_FOUND = "not_found"


class SupportType(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MENTIONS = "mentions"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


CONFIDENCE_ORDER: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


class CriterionCode(StrEnum):
    DEMAND = "demand"
    DISTRIBUTION = "distribution"
    COMPETITION = "competition"
    MONETIZATION = "monetization"
    TECHNICAL = "technical"
    AI_DEFENSIBILITY = "ai_defensibility"
    OPERATIONAL_LEGAL = "operational_legal"


class Revision(StrEnum):
    INITIAL = "initial"
    FINAL = "final"


class PriceStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    NOT_PUBLIC = "not_public"


class CompetitorCategory(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    SUBSTITUTE = "substitute"


class FinalVerdict(StrEnum):
    KEEP_RESEARCHING = "keep_researching"
    COMMERCIAL_VALIDATION = "commercial_validation"
    DISCARD = "discard"


class ClaimSourceRole(StrEnum):
    EVIDENCE = "evidence"
    COUNTEREVIDENCE = "counterevidence"
