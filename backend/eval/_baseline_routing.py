"""
Routing logic: AI score -> DIRECT_TO_COURT ya AI_REVIEW.

Deck ke hisaab se: urgent, highly complex ya criminal cases seedhe physical court jaate hain,
baaki chhote civil disputes AI review (-> student forum -> senior review) me.

Scores 0..1 ke hain. Agar frontend/AI service ne scores bheje to wahi use hote hain
(score_source="client"). Warna neeche ka rule-based heuristic chalta hai, jise baad me
Ollama/Qwen call se replace kiya ja sakta hai -- bas `score_case()` ka return same rakhna.
"""
import os
from datetime import date
from typing import Optional

DIRECT_TO_COURT = "DIRECT_TO_COURT"
AI_REVIEW = "AI_REVIEW"
ROUTES = (DIRECT_TO_COURT, AI_REVIEW)

URGENCY_THRESHOLD = float(os.getenv("URGENCY_THRESHOLD", "0.7"))
COMPLEXITY_THRESHOLD = float(os.getenv("COMPLEXITY_THRESHOLD", "0.7"))

URGENT_KEYWORDS = (
    "injunction", "stay order", "eviction", "bail", "arrest", "custody", "threat",
    "domestic violence", "urgent", "immediate", "harassment", "medical emergency",
)
COMPLEX_CASE_TYPES = {"criminal", "constitutional", "property", "corporate", "family"}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


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
    text = f"{title} {description}".lower()
    reasons: list[str] = []

    # ---- urgency ----
    urgency = 0.1
    hits = [k for k in URGENT_KEYWORDS if k in text]
    if hits:
        urgency += 0.35 + 0.1 * (len(hits) - 1)
        reasons.append(f"Urgent keywords in filing: {', '.join(hits)}")
    if deadline:
        days_left = (deadline - today).days
        if days_left <= 7:
            urgency += 0.45
            reasons.append(f"Deadline in {max(days_left, 0)} day(s)")
        elif days_left <= 30:
            urgency += 0.2
            reasons.append(f"Deadline within 30 days ({days_left} days)")
    if case_type.lower() == "criminal":
        urgency += 0.3
        reasons.append("Criminal matter")

    # ---- complexity ----
    complexity = 0.1
    if case_type.lower() in COMPLEX_CASE_TYPES:
        complexity += 0.3
        reasons.append(f"Case type '{case_type}' is typically complex")
    if claim_amount is not None:
        if claim_amount >= 1_000_000:
            complexity += 0.4
            reasons.append("High claim value (>= ₹10 lakh)")
        elif claim_amount >= 500_000:
            complexity += 0.25
            reasons.append("Substantial claim value (>= ₹5 lakh)")
    if evidence_count > 8:
        complexity += 0.2
        reasons.append(f"Large evidence set ({evidence_count} files)")
    if len(description) > 3000:
        complexity += 0.1

    return _clamp(urgency), _clamp(complexity), reasons


def decide_route(urgency: float, complexity: float, case_type: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if case_type.lower() == "criminal":
        reasons.append("Criminal cases go directly to court")
        return DIRECT_TO_COURT, reasons
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
