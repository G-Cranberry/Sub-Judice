"""Hold-out #1 (eval/data/holdout1_regression.csv). v0.3 rules WERE tuned against it, so it is now a regression set."""
from eval import routing_eval

from eval.data_loader import load_cases  # noqa: E402

HOLDOUT = load_cases("holdout1_regression")

if __name__ == "__main__":
    routing_eval.CASES = HOLDOUT
    print("-- hold-out, NEW logic --")
    routing_eval.run()
    print("-- hold-out, ORIGINAL logic --")
    from eval import _baseline_routing as b
    routing_eval.run(module=b)
