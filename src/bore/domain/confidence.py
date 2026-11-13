"""Base mínima de evidencia, confianza por criterio y global (6.3)."""

from __future__ import annotations

from dataclasses import dataclass

from bore.domain.enums import ClaimKind, Confidence, VerificationStatus
from bore.domain.scoring import CRITICAL


@dataclass(frozen=True)
class AuditedClaim:
    """Afirmación auditada relevante para un criterio, con sus dominios."""

    kind: ClaimKind
    status: VerificationStatus
    disputed: bool
    domains: frozenset[str]  # dominios registrables de sus fuentes


def has_min_evidence_base(claims: list[AuditedClaim], *, is_critical: bool) -> bool:
    """Base mínima de un criterio crítico: ≥1 fact verified, o ≥2 facts
    partially_supported de dos dominios registrables distintos."""
    if not is_critical:
        return True
    facts = [c for c in claims if c.kind is ClaimKind.FACT]
    if any(c.status is VerificationStatus.VERIFIED for c in facts):
        return True
    partials = [c for c in facts if c.status is VerificationStatus.PARTIALLY_SUPPORTED]
    partial_domains = {d for c in partials for d in c.domains}
    return len(partials) >= 2 and len(partial_domains) >= 2


def criterion_confidence(claims: list[AuditedClaim]) -> Confidence:
    """Tabla de confianza de 6.3 (calculada por código)."""
    supporting = [
        c
        for c in claims
        if c.status in (VerificationStatus.VERIFIED, VerificationStatus.PARTIALLY_SUPPORTED)
    ]
    verified = [c for c in supporting if c.status is VerificationStatus.VERIFIED]
    verified_domains = {d for c in verified for d in c.domains}
    support_domains = {d for c in supporting for d in c.domains}
    has_tainted = any(c.status is VerificationStatus.CONTRADICTED or c.disputed for c in claims)
    if len(verified) >= 3 and len(verified_domains) >= 2 and not has_tainted:
        return Confidence.HIGH
    if verified and len(supporting) >= 2 and len(support_domains) >= 2:
        return Confidence.MEDIUM
    return Confidence.LOW


def global_confidence(per_criterion: dict[str, Confidence]) -> Confidence:
    """La confianza global es la más baja de los cuatro criterios críticos."""
    order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    return min((per_criterion[c] for c in CRITICAL), key=lambda c: order[c])
