"""
Routing accuracy harness.

Labelled FICTIONAL cases, written from the pitch-deck rule:
  urgent / highly complex / criminal  -> DIRECT_TO_COURT
  manageable, non-serious civil       -> AI_REVIEW
Labels are our own judgement (no real court data), so this measures "does the code
implement the deck's rule", not real-world legal accuracy.

Run:  python -m eval.routing_eval
"""
from datetime import date, timedelta

import sys
from app import routing
from eval import _baseline_routing as baseline

from eval.data_loader import TODAY  # noqa: E402


def d(n):  # deadline n days from TODAY
    return TODAY + timedelta(days=n)


from eval.data_loader import load_cases  # noqa: E402  (cases live in eval/data/*.csv, not in code)

CASES = load_cases("routing_main")


def run(verbose=True, module=None):
    module = module or routing
    tp = fp = tn = fn = 0
    misses = []
    for title, desc, ctype, amt, dl, ev, expected in CASES:
        u, c, _ = module.score_case(title=title, description=desc, case_type=ctype,
                                     claim_amount=amt, deadline=dl, evidence_count=ev, today=TODAY)
        got, _ = module.decide_route(u, c, ctype)
        if got == expected:
            if expected == module.DIRECT_TO_COURT: tp += 1
            else: tn += 1
        else:
            misses.append((title, expected, got, round(u, 2), round(c, 2)))
            if expected == module.DIRECT_TO_COURT: fn += 1   # urgent case wrongly sent to AI
            else: fp += 1
    n = len(CASES)
    acc = (tp + tn) / n
    recall = tp / (tp + fn) if tp + fn else 1
    prec = tp / (tp + fp) if tp + fp else 1
    if verbose:
        print(f"cases={n}  accuracy={acc:.1%}  court-recall={recall:.1%}  court-precision={prec:.1%}")
        print(f"missed-urgent (false AI_REVIEW)={fn}   over-escalated (false DIRECT_TO_COURT)={fp}")
        for m in misses:
            print("  MISS:", m)
    return acc, recall, prec, misses


if __name__ == "__main__":
    if "--baseline" in sys.argv:
        run(module=baseline)
    else:
        run()
