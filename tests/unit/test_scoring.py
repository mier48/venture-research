from decimal import Decimal

import pytest

from bore.domain.scoring import WEIGHTS, passes_gates, screening_score, weighted_score


def test_weights_sum_100():
    assert sum(WEIGHTS.values()) == 100
    assert len(WEIGHTS) == 7


def test_weighted_score_exact():
    assert weighted_score({k: 8 for k in WEIGHTS}) == Decimal("8.00")
    scores = {
        "demand": 8,
        "distribution": 7,
        "competition": 7,
        "monetization": 7,
        "technical": 9,
        "ai_defensibility": 7,
        "operational_legal": 9,
    }
    assert weighted_score(scores) == Decimal("7.55")


def test_weighted_score_validation():
    with pytest.raises(ValueError):
        weighted_score({"demand": 5})
    with pytest.raises(ValueError):
        weighted_score({k: 11 for k in WEIGHTS})
    with pytest.raises(ValueError):
        weighted_score({k: True for k in WEIGHTS})


def test_passes_gates_threshold():
    scores = {k: 7 for k in WEIGHTS}
    assert passes_gates(scores, min_final=Decimal("7.0"), min_critical=6, min_any=4)
    assert not passes_gates(scores, min_final=Decimal("7.5"), min_critical=6, min_any=4)


def test_critical_min_blocks_despite_mean():
    scores = {
        "demand": 10,
        "distribution": 10,
        "competition": 10,
        "monetization": 5,
        "technical": 10,
        "ai_defensibility": 10,
        "operational_legal": 10,
    }
    # Media 9.25 pero monetization (crítico) < 6 -> no publica.
    assert not passes_gates(scores, min_final=Decimal("7.0"), min_critical=6, min_any=4)


def test_any_min_blocks():
    scores = {k: 9 for k in WEIGHTS} | {"operational_legal": 3}
    assert not passes_gates(scores, min_final=Decimal("7.0"), min_critical=6, min_any=4)


def test_screening_score():
    s = screening_score({"demand": 10, "distribution": 0 + 1, "competition": 1})
    assert s > 0
    with pytest.raises(ValueError):
        screening_score({"demand": 5, "distribution": 5})
