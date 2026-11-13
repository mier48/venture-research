import pytest

from bore.domain.deduplication import CandidateView, apply_semantic_groups, fingerprint
from bore.domain.enums import OpportunityStatus as S
from bore.domain.models import IllegalTransitionError, check_transition


def test_fingerprint_normalizes():
    assert fingerprint("Hólà, Mundo!", "mundo hola") == fingerprint("mundo hola", "hola mundo")
    assert fingerprint("a", "b") != fingerprint("c", "d")


def test_semantic_groups_keep_most_complete():
    cands = [
        CandidateView(id="a", fingerprint="f1", completeness=1),
        CandidateView(id="b", fingerprint="f2", completeness=5),
        CandidateView(id="c", fingerprint="f3", completeness=2),
    ]
    res = apply_semantic_groups(cands, [[0, 1, 2]])
    assert res[0].kept_id == "b"
    assert set(res[0].duplicate_ids) == {"a", "c"}


def test_transitions():
    check_transition(S.CANDIDATE, S.ELIGIBLE)
    check_transition(S.ELIGIBLE, S.SHORTLISTED)
    check_transition(S.SHORTLISTED, S.SCORED)
    check_transition(S.SCORED, S.PUBLISHED)
    with pytest.raises(IllegalTransitionError):
        check_transition(S.CANDIDATE, S.PUBLISHED)
    with pytest.raises(IllegalTransitionError):
        check_transition(S.PUBLISHED, S.CANDIDATE)
