"""HTTP API.

POST /api/v1/jobs                                submit recipients (bulk)
GET  /api/v1/jobs/{job_id}                       status and progress
GET  /api/v1/jobs/{job_id}/certificates          per-recipient results
GET  /api/v1/jobs/{job_id}/download              all generated PDFs as one ZIP
GET  /api/v1/certificates/{certificate_id}/download   one PDF
"""
import os
import re
import tempfile
import zipfile
from datetime import date
from typing import Iterator, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import Settings
from app.generator import CertificateRenderError, render_certificate
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import (
    CertificateOut,
    CertificatePage,
    JobCreate,
    JobCreated,
    JobStatusOut,
    RejectedRecipient,
)
from app.validation import validate_recipients
from app.worker import count_by_status, process_job

router = APIRouter(prefix="/api/v1")


def get_db(request: Request) -> Iterator[Session]:
    """One database session per request, closed when the request ends."""
    with request.app.state.session_factory() as db:
        yield db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("/jobs", response_model=JobCreated, status_code=202, tags=["jobs"])
def create_job(
    payload: JobCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobCreated:
    """Accept a bulk request and start generating in the background.

    Responds 202 Accepted immediately with a job id; the client then polls the
    status endpoint. Invalid rows are reported in the response and do not stop
    the valid rows.
    """
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Too many recipients: {len(payload.recipients)}. The maximum per "
                f"request is {settings.max_recipients_per_job}; split the list into several jobs."
            ),
        )

    issue_date = payload.issue_date or date.today()

    # Pre-flight: render one throwaway certificate. If the course or issuer
    # name cannot be drawn, every certificate would fail for the same reason,
    # so reject the request now with a clear message instead.
    try:
        render_certificate(
            recipient_name="Sample Recipient",
            course_name=payload.course_name,
            issuer_name=payload.issuer_name,
            issue_date=issue_date,
            certificate_id="preflight",
        )
    except CertificateRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    results = validate_recipients(payload.recipients)
    rejected = [
        RejectedRecipient(row=r.row, name=r.name, email=r.email, errors=r.errors)
        for r in results
        if not r.is_valid
    ]
    accepted = len(results) - len(rejected)

    if accepted == 0:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "No valid recipients in the request; no job was created.",
                "rejected_recipients": [r.model_dump() for r in rejected],
            },
        )

    # One row per recipient, valid or not, saved in a single transaction.
    job = Job(course_name=payload.course_name, issuer_name=payload.issuer_name, issue_date=issue_date)
    job.certificates = [
        Certificate(
            row_number=r.row,
            recipient_name=r.name,
            recipient_email=r.email,
            status=CertificateStatus.PENDING if r.is_valid else CertificateStatus.INVALID,
            error=None if r.is_valid else "; ".join(r.errors),
        )
        for r in results
    ]
    db.add(job)
    db.commit()

        # Runs after the response has been sent, in a worker thread.
    background_tasks.add_task(
        process_job, job.id, request.app.state.session_factory, settings.storage_dir
    )

    return JobCreated(
        job_id=job.id,
        status=job.status,
        total_recipients=len(results),
        accepted=accepted,
        rejected=len(rejected),
        rejected_recipients=rejected,
        status_url=f"/api/v1/jobs/{job.id}",
        certificates_url=f"/api/v1/jobs/{job.id}/certificates",
    )
FAILURE_PREVIEW_LIMIT = 100


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _certificate_out(certificate: Certificate) -> CertificateOut:
    generated = certificate.status == CertificateStatus.GENERATED
    return CertificateOut(
        certificate_id=certificate.id,
        row=certificate.row_number,
        recipient_name=certificate.recipient_name,
        recipient_email=certificate.recipient_email,
        status=certificate.status,
        error=certificate.error,
        download_url=f"/api/v1/certificates/{certificate.id}/download" if generated else None,
        generated_at=certificate.generated_at,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusOut, tags=["jobs"])
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobStatusOut:
    job = _get_job_or_404(db, job_id)
    counts = count_by_status(db, job.id)
    total = sum(counts.values())
    finished = total - counts[CertificateStatus.PENDING]

    failures = db.scalars(
        select(Certificate)
        .where(
            Certificate.job_id == job.id,
            Certificate.status.in_([CertificateStatus.FAILED, CertificateStatus.INVALID]),
        )
        .order_by(Certificate.row_number)
        .limit(FAILURE_PREVIEW_LIMIT)
    ).all()

    return JobStatusOut(
        job_id=job.id,
        status=job.status,
        course_name=job.course_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        total=total,
        counts=counts,
        progress_percent=round(100 * finished / total, 1) if total else 0.0,
        failures=[_certificate_out(c) for c in failures],
        certificates_url=f"/api/v1/jobs/{job.id}/certificates",
        download_url=f"/api/v1/jobs/{job.id}/download",
    )
def _file_safe(text: Optional[str]) -> str:
    """'Asha Rao' -> 'Asha_Rao', for use in a download file name."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_") or "certificate"


@router.get("/jobs/{job_id}/certificates", response_model=CertificatePage, tags=["certificates"])
def list_certificates(
    job_id: str,
    status: Optional[Literal["pending", "generated", "failed", "invalid"]] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> CertificatePage:
    """Per-recipient results, paginated. Filter with ?status=generated|failed|invalid|pending."""
    job = _get_job_or_404(db, job_id)

    conditions = [Certificate.job_id == job.id]
    if status is not None:
        conditions.append(Certificate.status == status)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions))
    certificates = db.scalars(
        select(Certificate).where(*conditions).order_by(Certificate.row_number).limit(limit).offset(offset)
    ).all()

    return CertificatePage(
        job_id=job.id,
        total=total,
        limit=limit,
        offset=offset,
        items=[_certificate_out(c) for c in certificates],
    )

@router.get("/certificates/{certificate_id}/download", tags=["certificates"])
def download_certificate(
    certificate_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Download one certificate as a PDF."""
    certificate = db.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if certificate.status != CertificateStatus.GENERATED:
        # The certificate exists but there is no file to give: explain why.
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This certificate has not been generated.",
                "status": certificate.status,
                "error": certificate.error,
            },
        )

    path = settings.storage_dir / certificate.file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file is missing from storage")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"certificate_{_file_safe(certificate.recipient_name)}.pdf",
    )

@router.get("/jobs/{job_id}/download", tags=["certificates"])
def download_job_archive(
    job_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Download every generated certificate of a job as a single ZIP file."""
    job = _get_job_or_404(db, job_id)
    if job.status in JobStatus.UNFINISHED:
        raise HTTPException(
            status_code=409,
            detail="The job is still being processed; download the archive once it has finished.",
        )

    certificates = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.row_number)
    ).all()
    if not certificates:
        raise HTTPException(status_code=404, detail="This job has no generated certificates")

    # Build the ZIP in a temporary file (not in memory), then delete it once sent.
    handle, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(handle)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for certificate in certificates:
            path = settings.storage_dir / certificate.file_path
            if path.is_file():
                # Row number prefix keeps names unique and in request order.
                archive.write(
                    path, arcname=f"{certificate.row_number:05d}_{_file_safe(certificate.recipient_name)}.pdf"
                )

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"certificates_{job.id}.zip",
        background=BackgroundTask(os.remove, zip_path),
    )