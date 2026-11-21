from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..custody_chain import anchor_signature, append_entry, verify_chain, verify_evidence_files
from ..database import get_db
from ..models import Case, CustodyEntry, Evidence
from ..schemas import AnchorOut, CustodyIn, CustodyOut, CustodyVerifyOut
from ..security import caller_is_staff, check_case_access, require_staff

router = APIRouter(prefix="/custody", tags=["custody"])

# Deck: "Officer details have restricted access." Entries by these roles show the officer's name
# only to staff (valid X-Staff-Token). Hashes still verify because the server uses the real values.
RESTRICTED_ROLES = {"case_officer"}


def _present(entry: CustodyEntry, staff: bool) -> CustodyOut:
    out = CustodyOut.model_validate(entry)
    if not staff and (entry.role or "").lower() in RESTRICTED_ROLES:
        out.handled_by = "[restricted]"
    return out


@router.post("", response_model=CustodyOut, status_code=201, dependencies=[Depends(require_staff)])
def add_custody_entry(body: CustodyIn, db: Session = Depends(get_db)):
    if not db.get(Case, body.case_id):
        raise HTTPException(404, "Case not found")
    if body.evidence_id is not None:
        ev = db.get(Evidence, body.evidence_id)
        if not ev or ev.case_id != body.case_id:
            raise HTTPException(404, "Evidence not found for this case")
    entry = append_entry(db, **body.model_dump())
    db.commit()
    return CustodyOut.model_validate(entry)


@router.get("", response_model=list[CustodyOut])
def list_custody(
    case_id: int,
    evidence_id: Optional[int] = None,
    staff: bool = Depends(caller_is_staff),
    x_staff_token: Optional[str] = Header(default=None),
    x_case_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    check_case_access(case, x_staff_token, x_case_token)
    q = select(CustodyEntry).where(CustodyEntry.case_id == case_id).order_by(CustodyEntry.id)
    if evidence_id is not None:
        q = q.where(CustodyEntry.evidence_id == evidence_id)
    return [_present(e, staff) for e in db.scalars(q).all()]


@router.get("/verify/{case_id}", response_model=CustodyVerifyOut)
def verify_custody(case_id: int, anchor_head: Optional[str] = None, anchor_entries: Optional[int] = None,
                   x_staff_token: Optional[str] = Header(default=None),
                   x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    check_case_access(case, x_staff_token, x_case_token)
    entries = db.scalars(select(CustodyEntry).where(CustodyEntry.case_id == case_id).order_by(CustodyEntry.id)).all()
    head = db.scalar(select(Case.custody_head).where(Case.id == case_id))  # fresh from DB, not the identity map
    broken = verify_chain(list(entries), expected_head=head)
    evidence = db.scalars(select(Evidence).where(Evidence.case_id == case_id).order_by(Evidence.id)).all()
    problems = verify_evidence_files(list(evidence))
    anchor_ok = None
    if anchor_head is not None and anchor_entries is not None:
        # An anchor saved OUTSIDE the DB earlier: the entry at that position must still have that hash.
        anchor_ok = 1 <= anchor_entries <= len(entries) and entries[anchor_entries - 1].entry_hash == anchor_head
    return CustodyVerifyOut(
        case_id=case_id, entries=len(entries), valid=broken is None, first_broken_entry_id=broken,
        evidence_checked=len(evidence), evidence_intact=not problems, evidence_problems=problems,
        anchor_ok=anchor_ok,
    )


@router.get("/anchor/{case_id}", response_model=AnchorOut)
def custody_anchor(case_id: int, x_staff_token: Optional[str] = Header(default=None),
                   x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    """Snapshot of the chain head. Save it somewhere the database admin cannot edit; pass it back to
    /custody/verify/{id}?anchor_head=..&anchor_entries=.. to prove the whole log was not rebuilt."""
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    check_case_access(case, x_staff_token, x_case_token)
    entries = db.scalars(select(CustodyEntry).where(CustodyEntry.case_id == case_id).order_by(CustodyEntry.id)).all()
    head = entries[-1].entry_hash if entries else None
    issued = datetime.now(timezone.utc).isoformat()
    return AnchorOut(case_id=case_id, entries=len(entries), head=head, issued_at=issued,
                     signature=anchor_signature(case_id, len(entries), head, issued))
