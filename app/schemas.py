"""Request and response shapes (Pydantic models)."""
from datetime import date, datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.generator import unsupported_characters

MAX_NAME_LENGTH = 80


class RecipientIn(BaseModel):
    """One recipient. Validated row by row in app/validation.py."""

    name: str
    email: EmailStr

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())  # trim and collapse inner whitespace
        if len(value) < 2:
            raise ValueError("must be at least 2 characters")
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"must be at most {MAX_NAME_LENGTH} characters")
        if not any(character.isalpha() for character in value):
            raise ValueError("must contain at least one letter")
        unsupported = unsupported_characters(value)
        if unsupported:
            raise ValueError(
                "contains characters the certificate template cannot render: "
                + " ".join(unsupported[:5])
            )
        return value


class JobCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "course_name": "Introduction to Machine Learning",
                "issuer_name": "Acme Academy",
                "issue_date": "2026-10-09",
                "recipients": [
                    {"name": "Asha Rao", "email": "asha.rao@example.com"},
                    {"name": "Daniel Okafor", "email": "daniel.okafor@example.com"},
                ],
            }
        },
    )

    course_name: str = Field(min_length=2, max_length=120)
    issuer_name: str = Field(min_length=2, max_length=80)
    issue_date: Optional[date] = Field(default=None, description="Defaults to today.")
    # Deliberately loose (List[Any], not List[RecipientIn]): with a strict type
    # one bad row would make FastAPI reject the WHOLE request. Rows are instead
    # validated one at a time so the valid ones can still go ahead.
    recipients: List[Any] = Field(min_length=1)

class RejectedRecipient(BaseModel):
    row: int
    name: Optional[str]
    email: Optional[str]
    errors: List[str]


class JobCreated(BaseModel):
    job_id: str
    status: str
    total_recipients: int
    accepted: int
    rejected: int
    rejected_recipients: List[RejectedRecipient]
    status_url: str
    certificates_url: str

# Datetimes are stored as naive UTC; label them as UTC on the way out so the
# client receives an unambiguous timestamp ("2026-10-09T10:00:00Z").
UTCDateTime = Annotated[datetime, AfterValidator(lambda value: value.replace(tzinfo=timezone.utc))]


class CertificateOut(BaseModel):
    certificate_id: str
    row: int
    recipient_name: Optional[str]
    recipient_email: Optional[str]
    status: str
    error: Optional[str]
    download_url: Optional[str]
    generated_at: Optional[UTCDateTime]


class JobStatusOut(BaseModel):
    job_id: str
    status: str
    course_name: str
    issuer_name: str
    issue_date: date
    error: Optional[str]
    created_at: UTCDateTime
    started_at: Optional[UTCDateTime]
    completed_at: Optional[UTCDateTime]
    total: int
    counts: Dict[str, int]
    progress_percent: float
    # Rows that did not get a certificate (first 100; the certificates
    # endpoint with ?status=failed or ?status=invalid returns all of them).
    failures: List[CertificateOut]
    certificates_url: str
    download_url: str

class CertificatePage(BaseModel):
    job_id: str
    total: int
    limit: int
    offset: int
    items: List[CertificateOut]