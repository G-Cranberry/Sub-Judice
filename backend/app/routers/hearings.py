from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..custody_chain import append_entry
from ..database import get_db
from ..drafts import render
from ..models import Case, DraftDocument, Hearing, utcnow
from ..schemas import DraftIn, DraftOut, HearingIn, HearingOut, HearingUpdate
from ..security import check_case_access, require_staff, require_staff_for_listing, staff_name

router = APIRouter(prefix="/hearings", tags=["hearings"])


def _naive_utc(dt: datetime) -> datetime:
    """Store everything as naive UTC. Timezone-aware input is CONVERTED (the old code just dropped the
    offset, which shifted an IST time by 5.5 hours). Naive input is taken to already be UTC."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _actor(token: Optional[str], claimed: Optional[str]) -> str:
    """A named staff token decides who is recorded; shared/dev token falls back to the name in the request."""
    who = staff_name(token)
    return claimed or "system" if who in (None, "staff", "dev-staff") else who


def _hearing(db: Session, hearing_id: int) -> Hearing:
    h = db.scalars(select(Hearing).where(Hearing.id == hearing_id).options(selectinload(Hearing.drafts))).first()
    if not h:
        raise HTTPException(404, "Hearing not found")
    return h


def _check_clash(db: Session, *, court: Optional[str], when: datetime, exclude_id: Optional[int] = None) -> None:
    """Same court + same time slot can't hold two scheduled hearings."""
    if not court:
        return
    q = select(Hearing.id).where(
        func.lower(Hearing.court) == court.lower(), Hearing.scheduled_at == when, Hearing.status == "SCHEDULED"
    )
    if exclude_id is not None:
        q = q.where(Hearing.id != exclude_id)
    if db.scalars(q).first() is not None:
        raise HTTPException(409, f"{court} already has a hearing scheduled at that time")


@router.post("", response_model=HearingOut, status_code=201, dependencies=[Depends(require_staff)])
def create_hearing(body: HearingIn, x_staff_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    if not db.get(Case, body.case_id):
        raise HTTPException(404, "Case not found")
    data = body.model_dump(exclude={"scheduled_by"})
    data["scheduled_at"] = _naive_utc(data["scheduled_at"])
    _check_clash(db, court=data.get("court"), when=data["scheduled_at"])
    h = Hearing(**data)
    db.add(h)
    db.flush()
    append_entry(
        db, case_id=h.case_id, action="HEARING_SCHEDULED", handled_by=_actor(x_staff_token, body.scheduled_by),
        notes=f"hearing {h.id} at {h.scheduled_at.isoformat()} ({h.court or 'court TBC'})",
    )
    db.commit()
    return HearingOut.model_validate(_hearing(db, h.id))


@router.get("", response_model=list[HearingOut])
def list_hearings(
    response: Response,
    case_id: Optional[int] = None,
    upcoming: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_staff_token: Optional[str] = Header(default=None),
    x_case_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    if case_id is None:
        require_staff_for_listing(x_staff_token)  # all-hearings listing is staff only
    else:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(404, "Case not found")
        check_case_access(case, x_staff_token, x_case_token)
    q = select(Hearing)
    if case_id is not None:
        q = q.where(Hearing.case_id == case_id)
    if upcoming:
        q = q.where(Hearing.scheduled_at >= utcnow(), Hearing.status == "SCHEDULED")
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.options(selectinload(Hearing.drafts)).order_by(Hearing.scheduled_at, Hearing.id).limit(limit).offset(offset)
    ).all()
    response.headers["X-Total-Count"] = str(total)
    return [HearingOut.model_validate(h) for h in rows]


@router.get("/{hearing_id}", response_model=HearingOut)
def get_hearing(hearing_id: int, x_staff_token: Optional[str] = Header(default=None),
                x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    h = _hearing(db, hearing_id)
    check_case_access(db.get(Case, h.case_id), x_staff_token, x_case_token)
    return HearingOut.model_validate(h)


@router.patch("/{hearing_id}", response_model=HearingOut, dependencies=[Depends(require_staff)])
def update_hearing(hearing_id: int, body: HearingUpdate, x_staff_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    h = _hearing(db, hearing_id)
    changes = body.model_dump(exclude_unset=True, exclude={"updated_by"})
    for required in ("scheduled_at", "status"):  # explicit null would violate NOT NULL -> was a 500
        if required in changes and changes[required] is None:
            raise HTTPException(422, f"{required} cannot be null")
    if "scheduled_at" in changes:
        changes["scheduled_at"] = _naive_utc(changes["scheduled_at"])
    new_court = changes.get("court", h.court)
    new_time = changes.get("scheduled_at", h.scheduled_at)
    new_status = changes.get("status", h.status)
    if new_status == "SCHEDULED":
        _check_clash(db, court=new_court, when=new_time, exclude_id=h.id)
    summary = ", ".join(f"{k}={v}" for k, v in changes.items() if k != "notes") or "notes"
    for k, v in changes.items():
        setattr(h, k, v)
    append_entry(
        db, case_id=h.case_id, action="HEARING_UPDATED", handled_by=_actor(x_staff_token, body.updated_by),
        notes=f"hearing {h.id}: {summary}",
    )
    db.commit()
    return HearingOut.model_validate(_hearing(db, h.id))


@router.post("/{hearing_id}/drafts", response_model=DraftOut, status_code=201, dependencies=[Depends(require_staff)])
def create_draft(hearing_id: int, body: DraftIn, x_staff_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    h = _hearing(db, hearing_id)
    case = db.get(Case, h.case_id)
    d = DraftDocument(
        hearing_id=h.id, case_id=h.case_id, doc_type=body.doc_type,
        content=render(body.doc_type, case, h, body.prepared_by),
    )
    db.add(d)
    append_entry(
        db, case_id=h.case_id, action="DRAFT_GENERATED", handled_by=_actor(x_staff_token, body.prepared_by),
        notes=f"{body.doc_type} for hearing {h.id}",
    )
    db.commit()
    db.refresh(d)
    return DraftOut.model_validate(d)


@router.get("/{hearing_id}/drafts", response_model=list[DraftOut])
def list_drafts(hearing_id: int, x_staff_token: Optional[str] = Header(default=None),
                x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    h = _hearing(db, hearing_id)
    check_case_access(db.get(Case, h.case_id), x_staff_token, x_case_token)
    return [DraftOut.model_validate(d) for d in h.drafts]
