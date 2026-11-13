"""Calibración 6.5 (offline): con las notas de referencia etiquetadas en los
fixtures, la rúbrica ordena todas las buenas por encima de las débiles."""

import json
from pathlib import Path

from bore.domain import scoring

FIXTURES = Path(__file__).parent.parent / "fixtures" / "calibration"


def load_fixtures():
    return [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))]


def test_fixtures_cover_eight_labeled_opportunities():
    fixtures = load_fixtures()
    assert len(fixtures) == 8
    labels = [f["label"] for f in fixtures]
    assert labels.count("good") == 4 and labels.count("weak") == 4
    for f in fixtures:
        assert set(f["criterion_scores"]) == set(scoring.WEIGHTS)


def test_rubric_ranks_all_good_above_all_weak():
    fixtures = load_fixtures()
    scored = [(f, scoring.weighted_score(f["criterion_scores"])) for f in fixtures]
    good = [s for f, s in scored if f["label"] == "good"]
    weak = [s for f, s in scored if f["label"] == "weak"]
    assert min(good) > max(weak)


# El test externo de estabilidad del evaluador real vive en
# tests/integration/test_external_openai.py (marker integration_external).
