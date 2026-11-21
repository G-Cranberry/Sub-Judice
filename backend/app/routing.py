"""
Routing logic: AI score -> DIRECT_TO_COURT or AI_REVIEW.

Deck rule: urgent, highly complex or criminal cases go straight to the physical court (judge gets the
priority flag + reasons); small, manageable civil disputes go to AI review -> student forum -> senior review.

Scores are 0..1. If the frontend / AI service sends scores they are used (score_source="client"/"mixed").
Otherwise the rule-based heuristic below runs. Later, plug Ollama/Qwen into `score_case()` - keep the
return shape (urgency, complexity, reasons) the same.

Design notes (why it is built this way):
* Keywords use word boundaries ("bail" must not match "bailment").
* Keywords are tiered: one CRITICAL term (injunction, domestic violence, ...) is enough to cross the
  urgency threshold by itself; softer terms need support from other signals.
* A deadline that is too close for the review pipeline (or already passed) forces court routing,
  because AI review + student opinions + senior review cannot finish in time.
* Scores are rounded to 4 decimals so float noise (0.1 + 0.6 != 0.7) never flips a route.
* Wrongly sending an urgent case to AI review is worse than the opposite, so thresholds err towards court.
"""
import os
import re
from datetime import date
from typing import Optional

DIRECT_TO_COURT = "DIRECT_TO_COURT"
AI_REVIEW = "AI_REVIEW"
ROUTES = (DIRECT_TO_COURT, AI_REVIEW)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


URGENCY_THRESHOLD = _env_float("URGENCY_THRESHOLD", 0.7)
COMPLEXITY_THRESHOLD = _env_float("COMPLEXITY_THRESHOLD", 0.7)
# Deadline this close (or already passed) is too short for AI review + student + senior review.
DEADLINE_URGENT_DAYS = int(_env_float("DEADLINE_URGENT_DAYS", 7))

# ---------------------------------------------------------------- vocabulary (data, not code)
# The keyword lists live in app/data/vocabulary.json (or the file named by VOCAB_FILE), so court staff / the
# team can add words, e.g. Hindi terms or new case types, without touching Python.
import json  # noqa: E402
from pathlib import Path  # noqa: E402


def _load_vocab() -> dict:
    path = Path(os.getenv("VOCAB_FILE") or Path(__file__).parent / "data" / "vocabulary.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _kw(pairs):
    return tuple((label, re.compile(rx, re.I)) for label, rx in pairs)


_VOCAB = _load_vocab()
CRITICAL_URGENT = _kw(_VOCAB["critical_urgent"])
SOFT_URGENT = _kw(_VOCAB["soft_urgent"])
MULTI_PARTY = re.compile(_VOCAB["multi_party"], re.I)
INHERENTLY_COMPLEX_TYPES = set(_VOCAB["inherently_complex_types"])   # alone they cross the complexity threshold
COMPLEX_TYPES = set(_VOCAB["complex_types"])                         # need more signals to cross it
CASE_TYPE_ALIASES = dict(_VOCAB["case_type_aliases"])


def normalize_case_type(raw: Optional[str]) -> str:
    t = re.sub(r"[\s\-]+", "_", (raw or "").strip().lower()) or "civil"
    return CASE_TYPE_ALIASES.get(t, t)


def _clamp(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 4)


def _hits(table, text: str) -> list[str]:
    return [label for label, rx in table if rx.search(text)]


# ---------------------------------------------------------------- scoring
def score_case(
    *,
    title: str,
    description: str,
    case_type: str,
    claim_amount: Optional[float],
    deadline: Optional[date],
    evidence_count: int,
    today: Optional[date] = None,
) -> tuple[float, float, list[str]]:
    """Returns (urgency, complexity, reasons)."""
    today = today or date.today()
    ctype = normalize_case_type(case_type)
    text = f"{title} {description}"
    reasons: list[str] = []

    # ---- urgency ----
    urgency = 0.1
    crit, soft = _hits(CRITICAL_URGENT, text), _hits(SOFT_URGENT, text)
    if crit:
        urgency += min(0.6 + 0.1 * (len(crit) - 1) + 0.1 * len(soft), 0.9)
        reasons.append(f"Critical urgency terms in filing: {', '.join(crit + soft)}")
    elif soft:
        urgency += min(0.35 + 0.1 * (len(soft) - 1), 0.9)
        reasons.append(f"Urgency terms in filing: {', '.join(soft)}")

    if deadline:
        days_left = (deadline - today).days
        if days_left < 0:
            urgency = max(urgency, URGENCY_THRESHOLD)
            reasons.append(f"Deadline passed {-days_left} day(s) ago; needs a judge's attention")
        elif days_left <= DEADLINE_URGENT_DAYS:
            urgency = max(urgency, URGENCY_THRESHOLD)
            reasons.append(
                f"Deadline in {days_left} day(s) is too short for AI + student + senior review"
            )
        elif days_left <= 30:
            urgency += 0.2
            reasons.append(f"Deadline within 30 days ({days_left} days)")
    if ctype == "criminal":
        urgency += 0.3
        reasons.append("Criminal matter")

    # ---- complexity ----
    complexity = 0.1
    if ctype in INHERENTLY_COMPLEX_TYPES:
        complexity += 0.6
        reasons.append(f"Case type '{ctype}' is inherently complex")
    elif ctype in COMPLEX_TYPES:
        complexity += 0.3
        reasons.append(f"Case type '{ctype}' is typically complex")
    if claim_amount is not None:
        if claim_amount >= 1_000_000:
            complexity += 0.4
            reasons.append("High claim value (>= ₹10 lakh)")
        elif claim_amount >= 500_000:
            complexity += 0.25
            reasons.append("Substantial claim value (>= ₹5 lakh)")
        elif claim_amount >= 200_000:
            complexity += 0.1
            reasons.append("Moderate claim value (>= ₹2 lakh)")
    if evidence_count > 8:
        complexity += 0.2
        reasons.append(f"Large evidence set ({evidence_count} files)")
    elif evidence_count > 5:
        complexity += 0.1
        reasons.append(f"Sizeable evidence set ({evidence_count} files)")
    if MULTI_PARTY.search(text):
        complexity += 0.15
        reasons.append("Multiple parties / heirs / partners involved")
    if len(description) > 3000:
        complexity += 0.1
        reasons.append("Very long description")

    return _clamp(urgency), _clamp(complexity), reasons


def score_case_ai(**kw) -> tuple[float, float, list[str], str]:
    """Rules + optional AI model. Returns (urgency, complexity, reasons, source).
    source = "heuristic" (rules only) or "ai+rules" (model answered; higher score of the two wins)."""
    from . import llm_scorer  # local import keeps routing importable without network code

    u, c, reasons = score_case(**kw)
    ai = llm_scorer.score(
        title=kw["title"], description=kw["description"], case_type=normalize_case_type(kw["case_type"]),
        claim_amount=kw.get("claim_amount"), deadline=kw.get("deadline"),
    )
    if ai is None:
        return u, c, reasons, "heuristic"
    au, ac, why = ai
    reasons = reasons + [f"AI model: urgency {au:.2f}, complexity {ac:.2f}" + (f" - {why}" if why else "")]
    if au > u or ac > c:
        reasons.append("AI model rated higher than the rules; the higher score is used")
    return _clamp(max(u, au)), _clamp(max(c, ac)), reasons, "ai+rules"


# ---------------------------------------------------------------- routing
def decide_route(urgency: float, complexity: float, case_type: str) -> tuple[str, list[str]]:
    ctype = normalize_case_type(case_type)
    urgency, complexity = round(urgency, 4), round(complexity, 4)
    if ctype == "criminal":
        return DIRECT_TO_COURT, ["Criminal cases go directly to court"]
    reasons: list[str] = []
    if urgency >= URGENCY_THRESHOLD:
        reasons.append(f"Urgency {urgency:.2f} >= threshold {URGENCY_THRESHOLD}")
    if complexity >= COMPLEXITY_THRESHOLD:
        reasons.append(f"Complexity {complexity:.2f} >= threshold {COMPLEXITY_THRESHOLD}")
    if reasons:
        return DIRECT_TO_COURT, reasons
    return AI_REVIEW, [
        f"Urgency {urgency:.2f} and complexity {complexity:.2f} are below thresholds: "
        "manageable civil dispute, sent to AI review"
    ]


def priority_for(urgency: float, complexity: float, case_type: str) -> str:
    """Flag shown to the judge: CRIMINAL | URGENT | COMPLEX | STANDARD."""
    if normalize_case_type(case_type) == "criminal":
        return "CRIMINAL"
    if round(urgency, 4) >= URGENCY_THRESHOLD:
        return "URGENT"
    if round(complexity, 4) >= COMPLEXITY_THRESHOLD:
        return "COMPLEX"
    return "STANDARD"
