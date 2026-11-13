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


def test_disputed_is_partially_supported():
    """Respaldo y contradicción confirmados a la vez: partially_supported +
    disputed (4.4), no verified."""
    st, disputed = claim_verification_status(
        [link(), link(qm=False, verdict=JudgeVerdict.CONTRADICTS)]
    )
    assert st == VerificationStatus.PARTIALLY_SUPPORTED and disputed


def test_judge_supports_verifies():
    st, _ = claim_verification_status([link(qm=False, verdict=JudgeVerdict.SUPPORTS)])
    assert st == VerificationStatus.VERIFIED


def test_quote_match_on_contradicts_link_confirms_contradiction():
    st, _ = claim_verification_status([link(qm=True, st="contradicts")])
    assert st == VerificationStatus.CONTRADICTED


def test_mentions_link_counts_for_nothing():
    st, _ = claim_verification_status([link(qm=True, st="mentions")])
    assert st == VerificationStatus.UNVERIFIED


def test_mentions_link_with_judge_verdict_counts():
    st, _ = claim_verification_status(
        [link(qm=False, verdict=JudgeVerdict.SUPPORTS, st="mentions")]
    )
    assert st == VerificationStatus.VERIFIED


def test_contradicts_link_not_provider_cited():
    """Un vínculo «contradicts» sin confirmación no vale como respaldo citado."""
    st, _ = claim_verification_status([link(qm=False, st="contradicts")])
    assert st == VerificationStatus.UNVERIFIED


def test_supports_link_inaccessible_still_provider_cited():
    st, _ = claim_verification_status([link(access=AccessStatus.BLOCKED, qm=False)])
    assert st == VerificationStatus.PARTIALLY_SUPPORTED


def test_support_and_quote_match_contradicts_is_disputed():
    st, disputed = claim_verification_status([link(), link(qm=True, st="contradicts")])
    assert st == VerificationStatus.PARTIALLY_SUPPORTED and disputed
