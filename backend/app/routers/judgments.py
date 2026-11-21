"""Judgments: one record, two versions (professional + plain language), drafted by staff, published, downloadable."""
import hashlib
import html
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import simplify as simplifier
from ..custody_chain import append_entry
from ..database import get_db
from ..models import Case, Hearing, Judgment, utcnow
from ..schemas import (JudgmentFormat, JudgmentIn, JudgmentOut, JudgmentPublishIn, JudgmentUpdate, JudgmentVersion)
from ..security import check_case_access, is_staff, require_staff, staff_name

router = APIRouter(prefix="/cases/{case_id}/judgments", tags=["judgments"])

SIMPLE_NOTICE = {
    "en": "PLAIN-LANGUAGE EXPLANATION. This is a simple summary to help you understand the judgment. "
          "It is not a substitute for the full judgment; if the two ever differ, the professional version is the one that counts.",
    "hi": "सरल भाषा में व्याख्या। यह निर्णय को समझने में मदद के लिए एक सरल सार है। "
          "यह पूरे निर्णय का विकल्प नहीं है; दोनों में अंतर होने पर पेशेवर (औपचारिक) संस्करण ही मान्य होगा।",
}


def fingerprint(case_id: int, title: str, professional: str, simple: str) -> str:
    return hashlib.sha256(json.dumps([case_id, title, professional, simple], ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _case(db: Session, case_id: int, staff_tok, case_tok) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    check_case_access(case, staff_tok, case_tok)
    return case


def _judgment(db: Session, case_id: int, jid: int, *, staff: bool) -> Judgment:
    j = db.get(Judgment, jid)
    # Drafts do not exist as far as the public is concerned (404, not 403).
    if not j or j.case_id != case_id or (j.status != "PUBLISHED" and not staff):
        raise HTTPException(404, "Judgment not found")
    return j


def _actor(token: Optional[str], claimed: Optional[str]) -> str:
    who = staff_name(token)
    return (claimed or "court staff") if who in (None, "staff", "dev-staff") else who


def _refresh_hash(j: Judgment) -> None:
    j.content_hash = fingerprint(j.case_id, j.title, j.professional_text, j.simple_text)


# ---------------------------------------------------------------- staff: write
@router.post("", response_model=JudgmentOut, status_code=201, dependencies=[Depends(require_staff)])
def create_judgment(case_id: int, body: JudgmentIn, x_staff_token: Optional[str] = Header(default=None),
                    db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    if body.hearing_id is not None:
        h = db.get(Hearing, body.hearing_id)
        if not h or h.case_id != case_id:
            raise HTTPException(404, "Hearing not found for this case")
    simple, source = (body.simple_text or "").strip(), "provided"
    if not simple:
        drafted = simplifier.simplify(body.professional_text, body.language) if body.auto_simplify else None
        simple, source = (drafted, "ai") if drafted else ("", "pending")
    j = Judgment(case_id=case_id, hearing_id=body.hearing_id, title=body.title,
                 professional_text=body.professional_text, simple_text=simple, simple_source=source,
                 language=body.language, issued_by=_actor(x_staff_token, body.issued_by), content_hash="")
    _refresh_hash(j)
    db.add(j)
    db.flush()
    append_entry(db, case_id=case_id, action="JUDGMENT_CREATED", handled_by=j.issued_by, role="court_staff",
                 notes=f"judgment {j.id} DRAFT; simple_version={source}; sha256={j.content_hash}")
    db.commit()
    return JudgmentOut.model_validate(j)


@router.patch("/{judgment_id}", response_model=JudgmentOut, dependencies=[Depends(require_staff)])
def edit_judgment(case_id: int, judgment_id: int, body: JudgmentUpdate,
                  x_staff_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    j = _judgment(db, case_id, judgment_id, staff=True)
    if j.status == "PUBLISHED":
        raise HTTPException(409, "A published judgment cannot be edited; issue a new judgment instead")
    ch = body.model_dump(exclude_unset=True, exclude={"resimplify"})
    for k, v in ch.items():
        setattr(j, k, v)
    if "simple_text" in ch:
        j.simple_source = "provided"  # a human wrote/edited it
    if body.resimplify:
        drafted = simplifier.simplify(j.professional_text, j.language)
        if not drafted:
            raise HTTPException(503, "AI simplification is unavailable (or the judgment is too long); write the simple version by hand")
        j.simple_text, j.simple_source = drafted, "ai"
    _refresh_hash(j)
    append_entry(db, case_id=case_id, action="JUDGMENT_EDITED", handled_by=_actor(x_staff_token, None), role="court_staff",
                 notes=f"judgment {j.id}: {', '.join(sorted(ch)) or 'resimplified'}; sha256={j.content_hash}")
    db.commit()
    return JudgmentOut.model_validate(j)


@router.post("/{judgment_id}/publish", response_model=JudgmentOut, dependencies=[Depends(require_staff)])
def publish_judgment(case_id: int, judgment_id: int, body: JudgmentPublishIn = JudgmentPublishIn(),
                     x_staff_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    j = _judgment(db, case_id, judgment_id, staff=True)
    if j.status == "PUBLISHED":
        raise HTTPException(409, "Already published")
    if not j.simple_text.strip():
        raise HTTPException(422, "Both versions are required: add the simple version (PATCH simple_text) before publishing")
    if j.simple_source == "ai" and not body.reviewed_simple_version:
        raise HTTPException(422, "The simple version was drafted by AI: review it, then publish with reviewed_simple_version=true")
    who = _actor(x_staff_token, None)
    j.status, j.published_at = "PUBLISHED", utcnow()
    case = db.get(Case, case_id)
    previous, case.status = case.status, "JUDGMENT_ISSUED"
    append_entry(db, case_id=case_id, action="JUDGMENT_PUBLISHED", handled_by=who, role="court_staff",
                 notes=f"judgment {j.id}; case status {previous} -> JUDGMENT_ISSUED; sha256={j.content_hash}")
    db.commit()
    return JudgmentOut.model_validate(j)


# ---------------------------------------------------------------- read + download
@router.get("", response_model=list[JudgmentOut])
def list_judgments(case_id: int, x_staff_token: Optional[str] = Header(default=None),
                   x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    _case(db, case_id, x_staff_token, x_case_token)
    q = select(Judgment).where(Judgment.case_id == case_id).order_by(Judgment.id)
    if not is_staff(x_staff_token):
        q = q.where(Judgment.status == "PUBLISHED")
    return [JudgmentOut.model_validate(j) for j in db.scalars(q).all()]


@router.get("/{judgment_id}", response_model=JudgmentOut)
def get_judgment(case_id: int, judgment_id: int, x_staff_token: Optional[str] = Header(default=None),
                 x_case_token: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    _case(db, case_id, x_staff_token, x_case_token)
    return JudgmentOut.model_validate(_judgment(db, case_id, judgment_id, staff=is_staff(x_staff_token)))


def _render_txt(case: Case, j: Judgment, version: str) -> str:
    when = (j.published_at or j.created_at).strftime("%d %B %Y")
    head = (f"SUB-JUDICE\nCase No.: {case.case_number}\nCase: {case.title}\nJudgment: {j.title}\n"
            f"Issued by: {j.issued_by}   Date: {when}\n")
    parts = []
    if version in ("professional", "both"):
        parts.append("JUDGMENT (PROFESSIONAL VERSION)\n" + "=" * 32 + "\n\n" + j.professional_text)
    if version in ("simple", "both"):
        parts.append(SIMPLE_NOTICE.get(j.language, SIMPLE_NOTICE["en"]) + "\n" + "=" * 32 + "\n\n" + j.simple_text)
    foot = f"\nDocument fingerprint (SHA-256): {j.content_hash}"
    if j.status != "PUBLISHED":
        head = "*** DRAFT - NOT YET PUBLISHED ***\n" + head
    return head + "\n" + "\n\n\n".join(parts) + "\n" + foot + "\n"


def _render_html(case: Case, j: Judgment, version: str) -> str:
    e = html.escape
    when = (j.published_at or j.created_at).strftime("%d %B %Y")

    def block(heading: str, text: str, cls: str, lang: str, note: str = "") -> str:
        body = "".join(f"<p>{e(p)}</p>" for p in text.split("\n\n") if p.strip()) or "<p></p>"
        return f'<section class="{cls}" lang="{lang}"><h2>{e(heading)}</h2>{f"<p class=note>{e(note)}</p>" if note else ""}{body}</section>'

    sections = []
    if version in ("professional", "both"):
        sections.append(block("Judgment (professional version)", j.professional_text, "pro", "en"))
    if version in ("simple", "both"):
        sections.append(block("In simple words" if j.language == "en" else "सरल भाषा में", j.simple_text, "simple", j.language,
                              SIMPLE_NOTICE.get(j.language, SIMPLE_NOTICE["en"])))
    draft = '<p class="draft">DRAFT - NOT YET PUBLISHED</p>' if j.status != "PUBLISHED" else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(case.case_number)} - {e(j.title)}</title>
<style>:root{{color-scheme:light dark}}body{{font:16px/1.6 system-ui,sans-serif;max-width:780px;margin:2rem auto;padding:0 1rem}}
h1{{font-size:1.4rem;margin:.2rem 0}}h2{{font-size:1.1rem;border-bottom:1px solid #8884;padding-bottom:.3rem}}.meta{{color:#888}}
.note{{font-size:.9rem;background:#8882;padding:.6rem .8rem;border-radius:6px}}.draft{{color:#b00;font-weight:700}}
section{{margin:1.8rem 0}}.fp{{font:12px monospace;color:#888;word-break:break-all}}@media print{{section.simple{{page-break-before:always}}}}</style>
</head><body>{draft}<p class="meta">Sub-Judice &middot; {e(case.case_number)}</p><h1>{e(j.title)}</h1>
<p class="meta">{e(case.title)} &middot; Issued by {e(j.issued_by)} &middot; {e(when)}</p>{''.join(sections)}
<p class="fp">Document fingerprint (SHA-256): {e(j.content_hash)}</p></body></html>"""


@router.get("/{judgment_id}/download")
def download_judgment(
    case_id: int, judgment_id: int,
    version: JudgmentVersion = Query("both", description="professional | simple | both"),
    format: JudgmentFormat = Query("txt", description="txt | html (open the html and Print -> Save as PDF)"),
    x_staff_token: Optional[str] = Header(default=None), x_case_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    case = _case(db, case_id, x_staff_token, x_case_token)
    j = _judgment(db, case_id, judgment_id, staff=is_staff(x_staff_token))
    if j.content_hash != fingerprint(j.case_id, j.title, j.professional_text, j.simple_text):
        raise HTTPException(409, "Stored judgment no longer matches its fingerprint; not served")
    if version in ("simple", "both") and not j.simple_text.strip():
        raise HTTPException(409, "This judgment has no simple version yet")
    who = staff_name(x_staff_token)
    append_entry(db, case_id=case_id, action="JUDGMENT_DOWNLOADED", handled_by=who if who else case.filer_name,
                 role="court_staff" if who else case.filer_type,
                 notes=f"judgment {j.id}; version={version}; format={format}; sha256={j.content_hash}")
    db.commit()
    text = _render_html(case, j, version) if format == "html" else _render_txt(case, j, version)
    name = f"{case.case_number}-judgment-{j.id}-{version}.{format}"
    media = "text/html; charset=utf-8" if format == "html" else "text/plain; charset=utf-8"
    return Response(content=text.encode("utf-8"), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
