"""Hold-out set #2. Written AFTER the v0.3 routing changes and run ONCE; do not tune against it.
(holdout_eval.py was used to tune v0.3, so it is now a regression set, not a hold-out.)
Includes deliberately hard cases (no trigger words / tricky wording). Labels are our own judgement."""
from eval import routing_eval

from eval.data_loader import load_cases  # noqa: E402

HOLDOUT2 = load_cases("holdout2")

if __name__ == "__main__":
    routing_eval.CASES = HOLDOUT2
    routing_eval.run()
