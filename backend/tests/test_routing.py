from datetime import date, timedelta

from app import routing
from eval import holdout_eval, routing_eval

TODAY = date(2026, 11, 21)


def score(**kw):
    base = dict(title="t", description="", case_type="civil", claim_amount=None, deadline=None, evidence_count=0, today=TODAY)
    base.update(kw)
    return routing.score_case(**base)


def route(**kw):
    u, c, _ = score(**kw)
    return routing.decide_route(u, c, kw.get("case_type", "civil"))[0]


def test_plain_small_dispute_is_ai_review():
    assert route(description="refund for a mixer", claim_amount=4000) == routing.AI_REVIEW


def test_single_critical_term_is_enough():
    for term in ("injunction", "domestic violence", "eviction notice", "stay order", "medical emergency"):
        assert route(description=f"need {term}") == routing.DIRECT_TO_COURT, term


def test_bailment_is_not_bail():
    assert route(description="goods lost in bailment") == routing.AI_REVIEW


def test_soft_term_alone_is_not_enough():
    assert route(description="please treat this as urgent") == routing.AI_REVIEW


def test_criminal_aliases_and_whitespace():
    for ct in ("criminal", "Criminal", "  CRIMINAL ", "criminal case", "Criminal-Complaint", "FIR"):
        assert route(case_type=ct) == routing.DIRECT_TO_COURT, ct


def test_deadline_boundaries():
    assert route(deadline=TODAY - timedelta(days=3)) == routing.DIRECT_TO_COURT   # already passed
    assert route(deadline=TODAY) == routing.DIRECT_TO_COURT
    assert route(deadline=TODAY + timedelta(days=7)) == routing.DIRECT_TO_COURT
    assert route(deadline=TODAY + timedelta(days=8)) == routing.AI_REVIEW
    assert route(deadline=TODAY + timedelta(days=60)) == routing.AI_REVIEW


def test_float_noise_never_flips_a_route():
    # 0.1 + 0.6 is 0.7 only after rounding; must still count as >= 0.7
    u, _, _ = score(description="there was an injunction")
    assert u == 0.7
    assert routing.decide_route(0.1 + 0.6, 0.1, "civil")[0] == routing.DIRECT_TO_COURT


def test_complexity_signals():
    assert route(case_type="corporate") == routing.DIRECT_TO_COURT
    assert route(case_type="constitutional") == routing.DIRECT_TO_COURT
    assert route(case_type="property") == routing.AI_REVIEW  # type alone is not enough
    assert route(case_type="property", claim_amount=2_000_000, evidence_count=9) == routing.DIRECT_TO_COURT


def test_scores_stay_in_range():
    u, c, _ = score(description="injunction eviction bail arrest threat urgent " * 50, case_type="criminal",
                    claim_amount=10**9, evidence_count=99, deadline=TODAY)
    assert 0 <= u <= 1 and 0 <= c <= 1


def test_priority_flag():
    assert routing.priority_for(0.1, 0.1, "criminal") == "CRIMINAL"
    assert routing.priority_for(0.9, 0.1, "civil") == "URGENT"
    assert routing.priority_for(0.1, 0.9, "civil") == "COMPLEX"
    assert routing.priority_for(0.1, 0.1, "civil") == "STANDARD"


# ---- regression floors on the labelled sets (see eval/) ----
def test_tuned_set_regression():
    acc, recall, precision, misses = routing_eval.run(verbose=False)
    assert recall == 1.0 and acc >= 0.97, misses


def test_holdout_regression():
    routing_eval.CASES, saved = holdout_eval.HOLDOUT, routing_eval.CASES
    try:
        acc, recall, precision, misses = routing_eval.run(verbose=False)
    finally:
        routing_eval.CASES = saved
    assert acc >= 0.90 and recall >= 0.80 and precision == 1.0, misses
