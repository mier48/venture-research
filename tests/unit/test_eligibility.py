from bore.domain.eligibility import RuleAssessment, decide
from bore.domain.enums import EligibilityAnswer as A
from bore.domain.enums import OpportunityStatus
from bore.domain.enums import RejectionCode as R


def ra(rule, answer):
    return RuleAssessment(rule=rule, answer=answer, reason="r")


ALL = [R.NOT_SOFTWARE, R.DATA_ACCESS, R.SALES_DEPENDENCY, R.LLM_WRAPPER, R.OVER_SCOPE, R.REDUNDANT]


def test_yes_rejects():
    d = decide([ra(r, A.YES if r == R.LLM_WRAPPER else A.NO) for r in ALL])
    assert d.status == OpportunityStatus.REJECTED and d.rejection_code == R.LLM_WRAPPER


def test_uncertain_data_access_manual_review():
    d = decide([ra(r, A.UNCERTAIN if r == R.DATA_ACCESS else A.NO) for r in ALL])
    assert d.status == OpportunityStatus.NEEDS_MANUAL_REVIEW


def test_uncertain_other_continues():
    d = decide([ra(r, A.UNCERTAIN if r == R.SALES_DEPENDENCY else A.NO) for r in ALL])
    assert d.status == OpportunityStatus.ELIGIBLE
