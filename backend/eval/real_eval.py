"""Score routing against REAL labelled cases (e.g. labelled by a lawyer / court clerk), not our own guesses.

    python -m eval.real_eval eval/real_labels.csv

CSV columns: title,description,case_type,claim_amount,deadline,evidence_count,label
label = DIRECT_TO_COURT or AI_REVIEW. deadline = YYYY-MM-DD or blank. Uses the AI model too if OLLAMA_URL is set.
Use >= 100 cases with at least 30 per label before trusting the number.
"""
import csv
import sys
from datetime import date

from app import routing


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield (r["title"], r["description"], r["case_type"] or "civil",
                   float(r["claim_amount"]) if r.get("claim_amount") else None,
                   date.fromisoformat(r["deadline"]) if r.get("deadline") else None,
                   int(r.get("evidence_count") or 0), r["label"].strip())


def main(path):
    tp = fp = tn = fn = 0
    for title, desc, ctype, amt, dl, ev, label in load(path):
        u, c, _, _ = routing.score_case_ai(title=title, description=desc, case_type=ctype,
                                            claim_amount=amt, deadline=dl, evidence_count=ev)
        got, _ = routing.decide_route(u, c, ctype)
        court = label == routing.DIRECT_TO_COURT
        if got == label:
            tp, tn = tp + court, tn + (not court)
        else:
            fn, fp = fn + court, fp + (not court)
            print(f"MISS expected={label} got={got}: {title}")
    n = tp + tn + fp + fn
    print(f"n={n} accuracy={(tp + tn) / max(n, 1):.1%} missed-urgent={fn} over-escalated={fp}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval/real_labels.csv")
