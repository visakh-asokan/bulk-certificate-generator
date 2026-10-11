"""Database tables.

A Job is one bulk request. It has one Certificate row per recipient in that
request, including recipients that were rejected by validation, so the job is
a complete record of what was asked for and what happened to each row.
"""
import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class JobStatus:
    PENDING = "pending"  # accepted, background work not started yet
    PROCESSING = "processing"  # certificates are being generated
    COMPLETED = "completed"  # every recipient got a certificate
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some did, some did not
    FAILED = "failed"  # no certificate could be generated

    UNFINISHED = (PENDING, PROCESSING)


class CertificateStatus:
    PENDING = "pending"  # valid recipient, waiting to be generated
    GENERATED = "generated"  # PDF written to storage
    FAILED = "failed"  # generation was attempted and raised an error
    INVALID = "invalid"  # rejected by validation, never attempted

    ALL = (PENDING, GENERATED, FAILED, INVALID)


def new_id() -> str:
    # Random UUIDs, not 1, 2, 3...: download URLs cannot be guessed by counting.
    return str(uuid.uuid4())


def utcnow() -> datetime:
    # Stored as naive UTC because SQLite has no timezone-aware datetime type.
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_name: Mapped[str] = mapped_column(String(120))
    issuer_name: Mapped[str] = mapped_column(String(80))
    issue_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING, index=True)
    # Only set if the job as a whole stopped unexpectedly (not per-certificate errors).
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    certificates: Mapped[List["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Certificate(Base):
    __tablename__ = "certificates"
    # Every status/progress query filters or groups by (job_id, status).
    __table_args__ = (Index("ix_certificates_job_status", "job_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    # 1-based position of this recipient in the request, so the client can
    # match an error back to the row in their own list.
    row_number: Mapped[int] = mapped_column(Integer)
    # Nullable because an invalid row may not have contained them at all.
    recipient_name: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    recipient_email: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    status: Mapped[str] = mapped_column(String(16), default=CertificateStatus.PENDING)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    # Path of the PDF relative to the storage folder; set once generated.
    file_path: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    job: Mapped[Job] = relationship(back_populates="certificates")