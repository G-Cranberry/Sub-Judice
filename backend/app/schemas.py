"""
Saare request/response shapes yahin hain. Shilpa ka API contract aaye to
field names sirf is file (aur routers me jahan Form fields hain) me badalne padenge.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Route = Literal["DIRECT_TO_COURT", "AI_REVIEW"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- cases ----------
class EvidenceOut(ORM):
    id: int
    filename: str
    content_type: Optional[str]
    size_bytes: int
    sha256: str
    uploaded_at: datetime


class OverrideOut(ORM):
    id: int
    previous_route: str
    new_route: str
    staff_name: str
    reason: str
    created_at: datetime


class CaseOut(ORM):
    id: int
    case_number: str
    title: str
    description: str
    case_type: str
    filer_type: str
    filer_name: str
    claim_amount: Optional[float]
    deadline: Optional[date]
    urgency_score: float
    complexity_score: float
    score_source: str
    source: str = "user"  # user | judiciary | ai : who put this case into the system
    submitted_by: Optional[str] = None
    priority: str  # CRIMINAL | URGENT | COMPLEX | STANDARD (flag shown to the judge)
    route: str
    route_reasons: list[str]
    routed_by: str
    status: str
    created_at: datetime
    evidence: list[EvidenceOut] = []
    overrides: list[OverrideOut] = []


class CaseCreatedOut(CaseOut):
    """Only the POST /cases response carries this. Store it: it is shown once and cannot be recovered."""
    access_token: str  # send as X-Case-Token to read this case / its custody log / its hearings later


CaseStatus = Literal["FILED", "AI_REVIEW", "SENT_TO_COURT", "JUDGMENT_ISSUED", "WITHDRAWN", "CLOSED"]


class CaseStatusUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: CaseStatus
    reason: Optional[str] = Field(default=None, max_length=2000)
    updated_by: Optional[str] = Field(default=None, max_length=200)  # ignored if the staff token has a name


class OverrideIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    new_route: Route
    reason: str = Field(min_length=3, max_length=2000)
    staff_name: str = Field(min_length=1, max_length=200)


# ---------- custody ----------
class CustodyIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    case_id: int
    evidence_id: Optional[int] = None
    action: str = Field(min_length=1, max_length=80, description="e.g. TRANSFERRED, VIEWED, VERIFIED, SEALED")
    handled_by: str = Field(min_length=1, max_length=200)
    role: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("action")
    @classmethod
    def _norm_action(cls, v: str) -> str:  # TRANSFERRED / transferred / "Transferred " -> TRANSFERRED
        return "_".join(v.upper().split())


class CustodyOut(ORM):
    id: int
    case_id: int
    evidence_id: Optional[int]
    action: str
    handled_by: str
    role: Optional[str]
    notes: Optional[str]
    timestamp: datetime
    prev_hash: str
    entry_hash: str


class EvidenceProblem(BaseModel):
    evidence_id: int
    problem: Literal["MISSING", "HASH_MISMATCH"]


class CustodyVerifyOut(BaseModel):
    case_id: int
    entries: int
    valid: bool  # log chain intact (and nothing cut off the end)
    first_broken_entry_id: Optional[int]  # -1 = newest entries were deleted
    evidence_checked: int = 0
    evidence_intact: bool = True  # every stored original still matches the hash taken at upload
    evidence_problems: list[EvidenceProblem] = []
    anchor_ok: Optional[bool] = None  # only set when ?anchor_head=&anchor_entries= were passed


class AnchorOut(BaseModel):
    """Store this OUTSIDE the database (email, another system, a notary). Later pass it to /custody/verify to
    prove the log was not rebuilt. `signature` is an HMAC (needs ANCHOR_SECRET or a staff token configured)."""
    case_id: int
    entries: int
    head: Optional[str]
    issued_at: str
    signature: Optional[str]


# ---------- hearings ----------
HearingStatus = Literal["SCHEDULED", "COMPLETED", "ADJOURNED", "CANCELLED"]
DocType = Literal["hearing_notice", "summons", "adjournment_application", "order_sheet"]


class HearingIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    case_id: int
    scheduled_at: datetime
    court: Optional[str] = Field(default=None, max_length=200)
    purpose: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=5000)
    scheduled_by: Optional[str] = Field(default=None, max_length=200)  # goes into the audit log


class HearingUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    scheduled_at: Optional[datetime] = None
    court: Optional[str] = Field(default=None, max_length=200)
    purpose: Optional[str] = Field(default=None, max_length=300)
    status: Optional[HearingStatus] = None
    notes: Optional[str] = Field(default=None, max_length=5000)
    updated_by: Optional[str] = Field(default=None, max_length=200)  # goes into the audit log


class DraftIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    doc_type: DocType
    prepared_by: Optional[str] = Field(default=None, max_length=200)


class DraftOut(ORM):
    id: int
    hearing_id: int
    case_id: int
    doc_type: str
    content: str
    created_at: datetime


class HearingOut(ORM):
    id: int
    case_id: int
    scheduled_at: datetime
    court: Optional[str]
    purpose: Optional[str]
    status: str
    notes: Optional[str]
    created_at: datetime
    drafts: list[DraftOut] = []


# ---------- bulk import (judiciary / AI) ----------
class CaseImportItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str
    filer_name: str
    description: str = ""
    case_type: str = "civil"
    filer_type: str = "individual"
    claim_amount: Optional[float] = None
    deadline: Optional[date] = None
    urgency_score: Optional[float] = None
    complexity_score: Optional[float] = None


class CaseImportIn(BaseModel):
    cases: list[CaseImportItem] = Field(min_length=1, max_length=200)


class CaseImportResult(BaseModel):
    index: int
    ok: bool
    id: Optional[int] = None
    case_number: Optional[str] = None
    route: Optional[str] = None
    access_token: Optional[str] = None
    error: Optional[str] = None


# ---------- judgments ----------
Language = Literal["en", "hi"]
JudgmentFormat = Literal["txt", "html"]
JudgmentVersion = Literal["professional", "simple", "both"]


class JudgmentIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=300)
    professional_text: str = Field(min_length=20, max_length=200_000)
    simple_text: Optional[str] = Field(default=None, max_length=100_000)  # leave empty to have the AI draft it
    language: Language = "en"  # language of the simple version
    hearing_id: Optional[int] = None
    issued_by: Optional[str] = Field(default=None, max_length=200)  # ignored if the staff token has a name
    auto_simplify: bool = True


class JudgmentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: Optional[str] = Field(default=None, min_length=3, max_length=300)
    professional_text: Optional[str] = Field(default=None, min_length=20, max_length=200_000)
    simple_text: Optional[str] = Field(default=None, min_length=1, max_length=100_000)
    language: Optional[Language] = None
    resimplify: bool = False  # ask the AI to redraft the simple version from the (new) professional text


class JudgmentPublishIn(BaseModel):
    reviewed_simple_version: bool = False  # must be true if the simple version was drafted by the AI


class JudgmentOut(ORM):
    id: int
    case_id: int
    hearing_id: Optional[int]
    title: str
    professional_text: str
    simple_text: str
    simple_source: str
    language: str
    status: str
    issued_by: str
    content_hash: str
    created_at: datetime
    published_at: Optional[datetime]
