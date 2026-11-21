import hashlib
import math
import os
import re
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import routing
from ..custody_chain import append_entry
from ..database import get_db
from ..models import Case, Evidence, RouteOverride
from ..custody_chain import sha256_file
from ..schemas import (CaseCreatedOut, CaseImportIn, CaseImportResult, CaseOut, CaseStatusUpdate, OverrideIn)
from ..security import (ai_token_ok, authorize_source, check_case_access, new_case_token, require_staff,
                        require_staff_for_listing, staff_name)

router = APIRouter(prefix="/cases", tags=["cases"])

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_FILES = int(os.getenv("MAX_FILES_PER_CASE", "20"))
_DEFAULT_EXT = "pdf,png,jpg,jpeg,tif,tiff,webp,txt,csv,doc,docx,xls,xlsx,mp3,wav,mp4,mov,eml,json"
ALLOWED_EXT = {e.strip().lower().lstrip(".") for e in os.getenv("ALLOWED_EXTENSIONS", _DEFAULT_EXT).split(",") if e.strip()}
ALLOW_ALL_EXT = "*" in ALLOWED_EXT
FILER_TYPES = ("individual", "law_firm")
_CASE_LOAD = (selectinload(Case.evidence), selectinload(Case.overrides))


def _opt_float(v: Optional[str], name: str) -> Optional[float]:
    if v is None or v.strip() == "":
        return None
    try:
        f = float(v)
    except ValueError:
        raise HTTPException(422, f"{name} must be a number")
    if not math.isfinite(f):
        raise HTTPException(422, f"{name} must be a finite number")
    return f


def _opt_date(v: Optional[str], name: str) -> Optional[date]:
    if v is None or v.strip() == "":
        return None
    try:
        return date.fromisoformat(v.strip()[:10])
    except ValueError:
        raise HTTPException(422, f"{name} must be YYYY-MM-DD")


def _status_for(route: str) -> str:
    return "SENT_TO_COURT" if route == routing.DIRECT_TO_COURT else "AI_REVIEW"


def _safe_name(raw: str) -> str:
    """Drop any path part, keep only harmless characters, cap length."""
    name = Path(raw.replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .")
    return name[-150:] or "file"


def _load(db: Session, case_id: int, *, lock: bool = False) -> Case:
    q = select(Case).where(Case.id == case_id).options(*_CASE_LOAD)
    if lock:
        q = q.with_for_update()  # Postgres row lock; ignored by SQLite
    case = db.scalars(q).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return case


def _store_files(db: Session, case: Case, real_files: list[UploadFile], written: list[Path], *, actor: str, role: str) -> None:
    """Writes files to disk + evidence rows + custody entries. Every path is appended to `written` BEFORE it is
    created, so the caller can delete exactly those if anything fails (never the whole case folder: older
    evidence must survive)."""
    if not real_files:
        return
    case_dir = UPLOAD_DIR / str(case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    for f in real_files:
        display = _safe_name(f.filename)
        stored = case_dir / f"{uuid.uuid4().hex}_{display}"
        written.append(stored)
        sha, size = hashlib.sha256(), 0
        with open(stored, "wb") as out:
            while chunk := f.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"{display} exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
                sha.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, f"{display} is empty")
        ev = Evidence(case_id=case.id, filename=display, stored_path=str(stored),
                      content_type=f.content_type, size_bytes=size, sha256=sha.hexdigest())
        db.add(ev)
        db.flush()
        append_entry(db, case_id=case.id, evidence_id=ev.id, action="EVIDENCE_UPLOADED",
                     handled_by=actor, role=role, notes=f"sha256={ev.sha256}")


def _check_files(real_files: list[UploadFile], already: int = 0) -> None:
    if already + len(real_files) > MAX_FILES:
        raise HTTPException(422, f"At most {MAX_FILES} files per case")
    for f in real_files:
        name = _safe_name(f.filename)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if not ALLOW_ALL_EXT and ext not in ALLOWED_EXT:
            raise HTTPException(422, f"File type '.{ext}' is not allowed ({f.filename})")


def _create(
    db: Session, *, title, filer_name, description, case_type, filer_type, claim_amount, deadline,
    urgency_score, complexity_score, real_files, source="user", submitted_by=None,
) -> tuple[Case, str]:
    """The ONE place a case is validated, scored, routed and stored - used by the web form, bulk import and
    anything else, so every channel (user / judiciary / AI) follows identical rules."""
    # ---- validate everything BEFORE touching the database or disk ----
    title, filer_name, description = title.strip(), filer_name.strip(), (description or "").strip()
    if not title or len(title) > 300:
        raise HTTPException(422, "title is required (max 300 characters)")
    if not filer_name or len(filer_name) > 200:
        raise HTTPException(422, "filer_name is required (max 200 characters)")
    if len(description) > 50_000:
        raise HTTPException(422, "description is too long (max 50,000 characters)")
    filer_type = (filer_type or "individual").strip().lower()
    if filer_type not in FILER_TYPES:
        raise HTTPException(422, f"filer_type must be one of {', '.join(FILER_TYPES)}")
    ctype = routing.normalize_case_type(case_type)
    if len(ctype) > 50:
        raise HTTPException(422, "case_type is too long")

    amount = _opt_float(claim_amount, "claim_amount")
    if amount is not None and amount < 0:
        raise HTTPException(422, "claim_amount cannot be negative")
    due = _opt_date(deadline, "deadline")
    u_in = _opt_float(urgency_score, "urgency_score")
    c_in = _opt_float(complexity_score, "complexity_score")
    for name, val in (("urgency_score", u_in), ("complexity_score", c_in)):
        if val is not None and not 0 <= val <= 1:
            raise HTTPException(422, f"{name} must be between 0 and 1")
    _check_files(real_files)

    # ---- score + route ----
    h_u, h_c, h_reasons, h_source = routing.score_case_ai(  # rules, plus the AI model if OLLAMA_URL is set
        title=title, description=description, case_type=ctype,
        claim_amount=amount, deadline=due, evidence_count=len(real_files),
    )
    urgency = u_in if u_in is not None else h_u
    complexity = c_in if c_in is not None else h_c
    route, route_reasons = routing.decide_route(urgency, complexity, ctype)
    if u_in is None and c_in is None:
        source_label, reasons = h_source, route_reasons + h_reasons
    else:
        source_label = "client" if (u_in is not None and c_in is not None) else "mixed"
        reasons = route_reasons + [f"Scores supplied by AI service ({source_label})"]
        if source_label == "mixed":
            reasons += h_reasons
        h_route, _ = routing.decide_route(h_u, h_c, ctype)  # (rules + model, whichever is higher)
        if h_route != route:  # safety net: tell the reviewer when the rules disagree with the supplied scores
            reasons.append(f"Note: rule-based check would route this to {h_route}; staff may wish to review")
    if source != "user":
        reasons.append(f"Case submitted by {source}" + (f" ({submitted_by})" if submitted_by else ""))

    token_plain, token_hash = new_case_token()
    case = Case(
        access_token_hash=token_hash, source=source, submitted_by=submitted_by or filer_name,
        title=title, description=description, case_type=ctype, filer_type=filer_type,
        filer_name=filer_name, claim_amount=amount, deadline=due,
        urgency_score=urgency, complexity_score=complexity, score_source=source_label,
        route=route, route_reasons=reasons, routed_by="AI", status=_status_for(route),
    )
    db.add(case)
    db.flush()  # id chahiye

    append_entry(db, case_id=case.id, action="CASE_FILED", handled_by=submitted_by or filer_name,
                 role=source if source != "user" else filer_type,
                 notes=f"source={source}; route={route}; priority={case.priority}")
    written: list[Path] = []
    new_id = case.id  # remember it: after a rollback the object no longer knows its id
    try:
        _store_files(db, case, real_files, written, actor=submitted_by or filer_name, role=filer_type)
        db.commit()
    except Exception:
        db.rollback()
        for pth in written:
            pth.unlink(missing_ok=True)
        try:
            (UPLOAD_DIR / str(new_id)).rmdir()  # only removes it if empty
        except OSError:
            pass
        raise
    return case, token_plain


@router.post("", response_model=CaseCreatedOut, status_code=201)
def create_case(  # sync def: FastAPI runs it in a threadpool, so disk I/O never blocks the event loop
    title: str = Form(...),
    filer_name: str = Form(...),
    description: str = Form(""),
    case_type: str = Form("civil"),
    filer_type: str = Form("individual"),  # individual | law_firm
    claim_amount: Optional[str] = Form(None),
    deadline: Optional[str] = Form(None),  # YYYY-MM-DD
    urgency_score: Optional[str] = Form(None),  # optional: AI service se aaya 0..1 score
    complexity_score: Optional[str] = Form(None),
    source: str = Form("user"),  # user | judiciary (needs X-Staff-Token) | ai (needs X-AI-Token)
    submitted_by: Optional[str] = Form(None),  # who/what submitted it, for judiciary/ai uploads
    files: list[UploadFile] = File(default=[]),
    x_staff_token: Optional[str] = Header(default=None),
    x_ai_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    source = (source or "user").strip().lower()
    actor = authorize_source(source, x_staff_token, x_ai_token)
    submitter = actor or None
    if source == "ai" and submitted_by and submitted_by.strip():
        submitter = submitted_by.strip()[:200]
    case, token = _create(
        db, title=title, filer_name=filer_name, description=description, case_type=case_type,
        filer_type=filer_type, claim_amount=claim_amount, deadline=deadline, urgency_score=urgency_score,
        complexity_score=complexity_score, real_files=[f for f in files if f.filename],
        source=source, submitted_by=submitter,
    )
    return CaseCreatedOut.model_validate({**CaseOut.model_validate(_load(db, case.id)).model_dump(), "access_token": token})


@router.post("/import", response_model=list[CaseImportResult], status_code=200)
def import_cases(
    body: CaseImportIn,
    source: str = Query("judiciary", description="judiciary (staff token) or ai (AI token)"),
    x_staff_token: Optional[str] = Header(default=None),
    x_ai_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Load many cases at once (e.g. from the court's existing system or the AI intake service).
    Each case is validated and routed on its own: one bad row does not stop the others."""
    source = source.strip().lower()
    if source == "user":
        raise HTTPException(422, "Bulk import is for source=judiciary or source=ai")
    actor = authorize_source(source, x_staff_token, x_ai_token)
    results: list[CaseImportResult] = []
    for i, it in enumerate(body.cases):
        try:
            case, token = _create(
                db, title=it.title, filer_name=it.filer_name, description=it.description, case_type=it.case_type,
                filer_type=it.filer_type, claim_amount=None if it.claim_amount is None else str(it.claim_amount),
                deadline=it.deadline.isoformat() if it.deadline else None,
                urgency_score=None if it.urgency_score is None else str(it.urgency_score),
                complexity_score=None if it.complexity_score is None else str(it.complexity_score),
                real_files=[], source=source, submitted_by=actor,
            )
            results.append(CaseImportResult(index=i, ok=True, id=case.id, case_number=case.case_number,
                                            route=case.route, access_token=token))
        except HTTPException as e:
            db.rollback()
            results.append(CaseImportResult(index=i, ok=False, error=str(e.detail)))
    return results


@router.post("/{case_id}/evidence", response_model=CaseOut, status_code=201)
def add_evidence(
    case_id: int,
    files: list[UploadFile] = File(...),
    x_staff_token: Optional[str] = Header(default=None),
    x_case_token: Optional[str] = Header(default=None),
    x_ai_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Attach more evidence to an existing case (filer with their case token, court staff, or the AI service)."""
    case = _load(db, case_id, lock=True)
    ai = ai_token_ok(x_ai_token)
    if not ai:
        check_case_access(case, x_staff_token, x_case_token)
    real_files = [f for f in files if f.filename]
    if not real_files:
        raise HTTPException(422, "No files uploaded")
    _check_files(real_files, already=len(case.evidence))
    who = "ai-service" if ai else staff_name(x_staff_token)
    by_staff = who is not None
    written: list[Path] = []
    try:
        _store_files(db, case, real_files, written, actor=who if by_staff else case.filer_name,
                               role="court_staff" if by_staff and not ai else ("ai" if ai else case.filer_type))
        db.commit()
    except Exception:
        db.rollback()
        for pth in written:
            pth.unlink(missing_ok=True)
        raise
    db.expire_all()  # the already-loaded evidence list is stale after adding rows; reload from the database
    return CaseOut.model_validate(_load(db, case_id))


@router.get("", response_model=list[CaseOut])
def list_cases(
    response: Response,
    route: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_staff_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    require_staff_for_listing(x_staff_token)
    if route and route not in routing.ROUTES:
        raise HTTPException(422, f"route must be one of {', '.join(routing.ROUTES)}")
    q = select(Case)
    if route:
        q = q.where(Case.route == route)
    if status:
        q = q.where(Case.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.options(*_CASE_LOAD).order_by(Case.id.desc()).limit(limit).offset(offset)).all()
    response.headers["X-Total-Count"] = str(total)
    return [CaseOut.model_validate(c) for c in rows]


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: int, x_staff_token: Optional[str] = Header(default=None),
             x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    case = _load(db, case_id)
    check_case_access(case, x_staff_token, x_case_token)
    return CaseOut.model_validate(case)


@router.post("/{case_id}/override", response_model=CaseOut, dependencies=[Depends(require_staff)])
def override_route(case_id: int, body: OverrideIn, x_staff_token: Optional[str] = Header(default=None),
                   db: Session = Depends(get_db)):
    case = _load(db, case_id, lock=True)
    actor = staff_name(x_staff_token)
    actor = body.staff_name if actor in (None, "staff", "dev-staff") else actor  # named token wins over the body
    if body.new_route == case.route:
        raise HTTPException(400, f"Case is already routed to {case.route}")
    previous = case.route
    case.overrides.append(RouteOverride(  # via the relationship so the already-loaded list stays in sync
        previous_route=previous, new_route=body.new_route,
        staff_name=actor, reason=body.reason,
    ))
    append_entry(
        db, case_id=case.id, action="ROUTE_OVERRIDDEN", handled_by=actor, role="court_staff",
        notes=f"{previous} -> {body.new_route}: {body.reason}",
    )
    case.route = body.new_route
    case.routed_by = actor
    case.status = _status_for(body.new_route)
    db.commit()
    return CaseOut.model_validate(_load(db, case.id))


@router.patch("/{case_id}/status", response_model=CaseOut, dependencies=[Depends(require_staff)])
def update_status(case_id: int, body: CaseStatusUpdate, x_staff_token: Optional[str] = Header(default=None),
                  db: Session = Depends(get_db)):
    """Cases are never deleted (legal record). Withdraw / close them with a status change; it is logged."""
    case = _load(db, case_id, lock=True)
    if body.status == case.status:
        raise HTTPException(400, f"Case status is already {case.status}")
    actor = staff_name(x_staff_token)
    actor = body.updated_by or "staff" if actor in (None, "staff", "dev-staff") else actor
    previous, case.status = case.status, body.status
    append_entry(db, case_id=case.id, action="STATUS_CHANGED", handled_by=actor, role="court_staff",
                 notes=f"{previous} -> {body.status}" + (f": {body.reason}" if body.reason else ""))
    db.commit()
    return CaseOut.model_validate(_load(db, case.id))


@router.get("/{case_id}/evidence/{evidence_id}/download")
def download_evidence(case_id: int, evidence_id: int, x_staff_token: Optional[str] = Header(default=None),
                      x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    """Serves the ORIGINAL file after re-checking its hash, and records the access in the custody chain."""
    case = _load(db, case_id)
    check_case_access(case, x_staff_token, x_case_token)
    ev = next((e for e in case.evidence if e.id == evidence_id), None)
    if ev is None:
        raise HTTPException(404, "Evidence not found")
    actual = sha256_file(ev.stored_path)
    if actual is None:
        raise HTTPException(410, "Stored file is missing")
    if actual != ev.sha256:
        raise HTTPException(409, "Stored file no longer matches its upload hash; not served")
    who = staff_name(x_staff_token)
    is_staff_call = who is not None
    append_entry(db, case_id=case.id, evidence_id=ev.id, action="EVIDENCE_DOWNLOADED",
                 handled_by=who if is_staff_call else case.filer_name,
                 role="court_staff" if is_staff_call else case.filer_type, notes=f"sha256={ev.sha256}")
    db.commit()
    return FileResponse(ev.stored_path, media_type=ev.content_type or "application/octet-stream", filename=ev.filename)
