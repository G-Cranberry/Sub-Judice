"""Hash-chained, append-only evidence custody log + evidence-file integrity check."""
import hashlib
import json
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Case, CustodyEntry, Evidence, utcnow

GENESIS = "0" * 64


def _hash(prev_hash, case_id, evidence_id, action, handled_by, role, notes, ts) -> str:
    # JSON (not "|".join) so a "|" inside notes/handled_by can never shift fields and forge a collision.
    payload = json.dumps(
        [prev_hash, case_id, evidence_id, action, handled_by, role, notes, ts.isoformat()],
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _legacy_hash(prev_hash, case_id, evidence_id, action, handled_by, role, notes, ts) -> str:
    """v0.1 format. Only used to VERIFY old rows; new rows always use _hash()."""
    payload = "|".join(
        [prev_hash, str(case_id), str(evidence_id), action, handled_by, role or "", notes or "", ts.isoformat()]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def append_entry(
    db: Session,
    *,
    case_id: int,
    action: str,
    handled_by: str,
    evidence_id: Optional[int] = None,
    role: Optional[str] = None,
    notes: Optional[str] = None,
) -> CustodyEntry:
    # Take the case's write lock FIRST (row lock on Postgres, write lock on SQLite) so concurrent
    # appends are serialised and each one sees the true latest entry.
    db.execute(
        update(Case).where(Case.id == case_id).values(custody_seq=Case.custody_seq + 1)
        .execution_options(synchronize_session=False)
    )
    last = db.scalars(
        select(CustodyEntry).where(CustodyEntry.case_id == case_id).order_by(CustodyEntry.id.desc()).limit(1)
    ).first()
    prev = last.entry_hash if last else GENESIS
    ts = utcnow()
    entry = CustodyEntry(
        case_id=case_id, evidence_id=evidence_id, action=action, handled_by=handled_by,
        role=role, notes=notes, timestamp=ts, prev_hash=prev,
        entry_hash=_hash(prev, case_id, evidence_id, action, handled_by, role, notes, ts),
    )
    db.add(entry)
    db.flush()
    db.execute(
        update(Case).where(Case.id == case_id).values(custody_head=entry.entry_hash)
        .execution_options(synchronize_session=False)
    )
    return entry


def verify_chain(entries: list[CustodyEntry], expected_head: Optional[str] = None) -> Optional[int]:
    """Returns id of first broken entry (or -1 if the tail was cut off), None if chain is fine.

    expected_head = hash stored on the case; if given, it must equal the last entry's hash,
    which catches deletion of the newest entries."""
    prev = GENESIS
    for e in entries:
        args = (prev, e.case_id, e.evidence_id, e.action, e.handled_by, e.role, e.notes, e.timestamp)
        if e.prev_hash != prev or e.entry_hash not in (_hash(*args), _legacy_hash(*args)):
            return e.id
        prev = e.entry_hash
    if expected_head is not None and expected_head != prev:
        return -1
    return None


def sha256_file(path: str) -> Optional[str]:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def verify_evidence_files(evidence: list[Evidence]) -> list[dict]:
    """Re-hash every stored original; returns [{evidence_id, problem}] for missing/modified files."""
    problems = []
    for ev in evidence:
        actual = sha256_file(ev.stored_path)
        if actual is None:
            problems.append({"evidence_id": ev.id, "problem": "MISSING"})
        elif actual != ev.sha256:
            problems.append({"evidence_id": ev.id, "problem": "HASH_MISMATCH"})
    return problems


def anchor_signature(case_id: int, entries: int, head: Optional[str], issued_at: str) -> Optional[str]:
    """HMAC over the anchor fields. Secret = ANCHOR_SECRET, else the first configured staff token; None if neither."""
    import hmac
    import os
    from .security import _staff_map

    secret = os.getenv("ANCHOR_SECRET") or next(iter(_staff_map()), "")
    if not secret:
        return None
    msg = json.dumps([case_id, entries, head, issued_at], separators=(",", ":"))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
