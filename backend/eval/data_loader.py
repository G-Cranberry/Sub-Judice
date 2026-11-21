"""Eval cases live in eval/data/*.csv (not in code). Add/edit rows freely.
Columns: title,description,case_type,claim_amount,deadline_days,evidence_count,label
deadline_days = days from the eval's fixed 'today' (negative = already passed); blank = no deadline."""
import csv
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "data"
TODAY = date(2026, 11, 21)  # fixed so results never change with the calendar


def load_cases(name_or_path: str):
    p = Path(name_or_path)
    if not p.exists():
        p = DATA / f"{name_or_path}.csv"
    out = []
    with open(p, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append((
                r["title"], r["description"], r["case_type"] or "civil",
                float(r["claim_amount"]) if r.get("claim_amount") else None,
                TODAY + timedelta(days=int(r["deadline_days"])) if r.get("deadline_days") not in (None, "") else None,
                int(r.get("evidence_count") or 0), r["label"].strip(),
            ))
    return out
