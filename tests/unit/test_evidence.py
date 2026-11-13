from bore.domain.enums import AccessStatus, JudgeVerdict, Provenance, VerificationStatus
from bore.domain.evidence import (
    ClaimSourceLink,
    canonical_url,
    claim_verification_status,
    quote_match,
    registrable_domain,
)

PA = Provenance.PROVIDER_ANNOTATION
SYN = Provenance.SYNTHETIC


def link(prov=PA, access=AccessStatus.OK, qm=True, verdict=None, st="supports"):
    return ClaimSourceLink(prov, access, qm, verdict, st)


def test_canonical_url():
    assert (
        canonical_url("HTTPS://Example.COM/path/?utm_source=x&a=1#frag")
        == "https://example.com/path/?a=1"
    )


def test_registrable_domain():
    assert registrable_domain("https://a.b.example.co.uk/x") == "example.co.uk"
    assert registrable_domain("https://sub.example.com") == "example.com"


def test_quote_match_normalizes():
    assert quote_match("  “Hola  mundo” ", "texto “hola mundo” y más")
    assert not quote_match("no está", "otra cosa")
    assert not quote_match(None, "algo")


def test_status_verified():
    st, disputed = claim_verification_status([link()])
    assert st == VerificationStatus.VERIFIED and not disputed


def test_status_synthetic_needs_flag():
    st, _ = claim_verification_status([link(prov=SYN)], allow_synthetic=False)
    assert st == VerificationStatus.UNVERIFIED
    st, _ = claim_verification_status([link(prov=SYN)], allow_synthetic=True)
    assert st == VerificationStatus.VERIFIED


def test_partially_supported_blocked():
    st, _ = claim_verification_status([link(access=AccessStatus.BLOCKED, qm=False)])
    assert st == VerificationStatus.PARTIALLY_SUPPORTED


def test_contradicted():
    st, disputed = claim_verification_status([link(qm=False, verdict=JudgeVerdict.CONTRADICTS)])
    assert st == VerificationStatus.CONTRADICTED and not disputed


def test_disputed():
    st, disputed = claim_verification_status(
        [link(), link(qm=False, verdict=JudgeVerdict.CONTRADICTS)]
    )
    assert st == VerificationStatus.VERIFIED and disputed


def test_judge_supports_verifies():
    st, _ = claim_verification_status([link(qm=False, verdict=JudgeVerdict.SUPPORTS)])
    assert st == VerificationStatus.VERIFIED
