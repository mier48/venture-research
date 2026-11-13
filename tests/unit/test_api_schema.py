import pytest
from pydantic import ValidationError

from bore.api.schemas import ResearchRunCreate


def test_defaults_ok():
    c = ResearchRunCreate(sector="inmobiliario")
    cfg = c.to_config()
    assert cfg["limits"]["max_duration_seconds"] == 5400
    assert c.review_limit >= c.requested_ideas


def test_review_limit_gte_requested():
    with pytest.raises(ValidationError):
        ResearchRunCreate(sector="x", requested_ideas=8, review_limit=7)


def test_review_limit_lte_deep_dive():
    with pytest.raises(ValidationError):
        ResearchRunCreate(sector="x", deep_dive_limit=4, review_limit=6, requested_ideas=2)


def test_pool_bounds():
    with pytest.raises(ValidationError):
        ResearchRunCreate(sector="x", candidate_pool_size=31)
