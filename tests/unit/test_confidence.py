from bore.domain.confidence import (
    AuditedClaim,
    criterion_confidence,
    global_confidence,
    has_min_evidence_base,
)
from bore.domain.enums import ClaimKind, Confidence, VerificationStatus

F = ClaimKind.FACT
V = VerificationStatus.VERIFIED
P = VerificationStatus.PARTIALLY_SUPPORTED
C = VerificationStatus.CONTRADICTED


def cl(status=V, kind=F, domains=frozenset({"a.com"}), disputed=False):
    return AuditedClaim(kind, status, disputed, domains)


def test_min_base():
    assert has_min_evidence_base([cl()], is_critical=True)
    assert not has_min_evidence_base([cl(status=P)], is_critical=True)
    assert has_min_evidence_base(
        [cl(status=P, domains=frozenset({"a.com"})), cl(status=P, domains=frozenset({"b.com"}))],
        is_critical=True,
    )
    assert not has_min_evidence_base(
        [cl(status=P), cl(status=P)], is_critical=True
    )  # mismo dominio
    assert has_min_evidence_base([], is_critical=False)


def test_confidence_high_medium_low():
    high = [
        cl(domains=frozenset({"a.com"})),
        cl(domains=frozenset({"b.com"})),
        cl(domains=frozenset({"b.com"})),
    ]
    assert criterion_confidence(high) == Confidence.HIGH
    medium = [cl(domains=frozenset({"a.com"})), cl(status=P, domains=frozenset({"b.com"}))]
    assert criterion_confidence(medium) == Confidence.MEDIUM
    assert criterion_confidence([cl()]) == Confidence.LOW
    tainted = [*high, cl(status=C)]
    assert criterion_confidence(tainted) != Confidence.HIGH


def test_global_confidence():
    conf = {
        "demand": Confidence.HIGH,
        "distribution": Confidence.MEDIUM,
        "competition": Confidence.HIGH,
        "monetization": Confidence.LOW,
        "technical": Confidence.HIGH,
        "ai_defensibility": Confidence.HIGH,
        "operational_legal": Confidence.HIGH,
    }
    assert global_confidence(conf) == Confidence.LOW
