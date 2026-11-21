from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import routing
from .database import Base


def utcnow() -> datetime:
    """Naive UTC datetime (SQLite aur Postgres dono me same behaviour)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    case_type: Mapped[str] = mapped_column(String(50), default="civil")
    filer_type: Mapped[str] = mapped_column(String(30), default="individual")
    filer_name: Mapped[str] = mapped_column(String(200))
    claim_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    urgency_score: Mapped[float] = mapped_column(Float, default=0.0)
    complexity_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_source: Mapped[str] = mapped_column(String(20), default="heuristic")  # heuristic | client
    route: Mapped[str] = mapped_column(String(30), index=True)  # DIRECT_TO_COURT | AI_REVIEW
    route_reasons: Mapped[list] = mapped_column(JSON, default=list)
    routed_by: Mapped[str] = mapped_column(String(200), default="AI")
    status: Mapped[str] = mapped_column(String(30), default="FILED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Custody-chain anchor: hash of the newest entry (detects deleted tail entries) and a counter that
    # doubles as a row lock so two writers can never fork the chain.
    custody_head: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    custody_seq: Mapped[int] = mapped_column(Integer, default=0)
    # sha256 of the filer's one-time access token (the plain token is shown only in the POST /cases response)
    access_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Who put the case into the system: the public ("user"), court staff ("judiciary") or the AI service ("ai").
    source: Mapped[str] = mapped_column(String(20), default="user", index=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="Evidence.id"
    )
    overrides: Mapped[list["RouteOverride"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="RouteOverride.id"
    )

    @property
    def case_number(self) -> str:
        return f"SJ-{self.created_at.year}-{self.id:05d}"

    @property
    def priority(self) -> str:
        """Flag for the judge: CRIMINAL | URGENT | COMPLEX | STANDARD."""
        return routing.priority_for(self.urgency_score, self.complexity_score, self.case_type)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="evidence")


class RouteOverride(Base):
    __tablename__ = "route_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    previous_route: Mapped[str] = mapped_column(String(30))
    new_route: Mapped[str] = mapped_column(String(30))
    staff_name: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="overrides")


class CustodyEntry(Base):
    """Append-only, hash-chained log. Har entry apne se pehle wali entry ka hash include karti hai."""

    __tablename__ = "custody_entries"
    # One successor per entry: a forked chain is rejected by the database itself.
    __table_args__ = (UniqueConstraint("case_id", "prev_hash", name="uq_custody_chain_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    evidence_id: Mapped[Optional[int]] = mapped_column(ForeignKey("evidence.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    handled_by: Mapped[str] = mapped_column(String(200))
    role: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


class Hearing(Base):
    __tablename__ = "hearings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    court: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="SCHEDULED", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    drafts: Mapped[list["DraftDocument"]] = relationship(
        back_populates="hearing", cascade="all, delete-orphan", order_by="DraftDocument.id"
    )


class DraftDocument(Base):
    __tablename__ = "draft_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hearing_id: Mapped[int] = mapped_column(ForeignKey("hearings.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    hearing: Mapped[Hearing] = relationship(back_populates="drafts")


class Judgment(Base):
    """A judgment kept in TWO versions: the professional (authoritative) text and a plain-language explanation.
    DRAFT is visible to staff only; PUBLISHED is visible to the filer and downloadable. Published = immutable."""

    __tablename__ = "judgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    hearing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hearings.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    professional_text: Mapped[str] = mapped_column(Text)
    simple_text: Mapped[str] = mapped_column(Text, default="")
    simple_source: Mapped[str] = mapped_column(String(20), default="pending")  # pending | provided | ai
    language: Mapped[str] = mapped_column(String(10), default="en")  # language of the simple version
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)  # DRAFT | PUBLISHED
    issued_by: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))  # fingerprint of title + both texts
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
