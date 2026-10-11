"""Background processing: turn a job's pending certificates into PDFs.

`process_job` only needs a job id. Everything else is read from the database,
so the same function could be handed to a task queue (Celery, RQ) unchanged.
It only ever picks up certificates that are still `pending`, which makes it
safe to run again on a job that was interrupted half-way.
"""
import logging
import os
from pathlib import Path
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.generator import render_certificate
from app.models import Certificate, CertificateStatus, Job, JobStatus, utcnow

logger = logging.getLogger(__name__)

BATCH_SIZE = 200  # certificates loaded into memory at a time


def save_pdf(storage_dir: Path, relative_path: str, content: bytes) -> None:
    """Write the PDF so that a half-written file can never look finished."""
    target = storage_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)  # atomic rename

def count_by_status(db: Session, job_id: str) -> Dict[str, int]:
    """Certificate counts per status, computed from the rows themselves.

    Counting rows (instead of keeping counters on the job) means the numbers
    can never drift out of step with what actually happened.
    """
    counts = {status: 0 for status in CertificateStatus.ALL}
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    for status, count in rows:
        counts[status] = count
    return counts


def final_job_status(counts: Dict[str, int]) -> str:
    if counts[CertificateStatus.GENERATED] == 0:
        return JobStatus.FAILED
    if counts[CertificateStatus.FAILED] or counts[CertificateStatus.INVALID]:
        return JobStatus.COMPLETED_WITH_ERRORS
    return JobStatus.COMPLETED

def _generate_one(db: Session, job: Job, certificate: Certificate, storage_dir: Path) -> None:
    """Generate a single certificate. Never raises for a certificate-level problem."""
    try:
        pdf_bytes = render_certificate(
            recipient_name=certificate.recipient_name,
            course_name=job.course_name,
            issuer_name=job.issuer_name,
            issue_date=job.issue_date,
            certificate_id=certificate.id,
        )
        relative_path = f"certificates/{job.id}/{certificate.id}.pdf"
        save_pdf(storage_dir, relative_path, pdf_bytes)

        certificate.status = CertificateStatus.GENERATED
        certificate.file_path = relative_path
        certificate.generated_at = utcnow()
        certificate.error = None
    except Exception as exc:
        # Deliberately broad: whatever goes wrong with THIS certificate is
        # recorded on its row, and the loop moves on to the next recipient.
        logger.warning("Certificate %s (row %s) failed: %s", certificate.id, certificate.row_number, exc)
        certificate.status = CertificateStatus.FAILED
        certificate.error = f"{type(exc).__name__}: {exc}"[:500]

    # Commit per certificate: progress is visible to status requests straight
    # away, and a crash loses at most the one certificate in flight.
    db.commit()

def process_job(job_id: str, session_factory: sessionmaker, storage_dir: Path) -> None:
    # A background thread must use its own session, never the request's.
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.error("Job %s not found; nothing to process", job_id)
            return

        try:
            job.status = JobStatus.PROCESSING
            job.started_at = job.started_at or utcnow()
            db.commit()

            while True:
                batch = db.scalars(
                    select(Certificate)
                    .where(
                        Certificate.job_id == job.id,
                        Certificate.status == CertificateStatus.PENDING,
                    )
                    .order_by(Certificate.row_number)
                    .limit(BATCH_SIZE)
                ).all()
                if not batch:
                    break
                for certificate in batch:
                    _generate_one(db, job, certificate, storage_dir)

            job.status = final_job_status(count_by_status(db, job.id))
        except Exception as exc:
            # Something outside a single certificate broke (e.g. the database).
            logger.exception("Job %s stopped unexpectedly", job_id)
            db.rollback()
            job.status = JobStatus.FAILED
            job.error = f"Job stopped unexpectedly: {type(exc).__name__}: {exc}"[:500]

        job.completed_at = utcnow()
        db.commit()
        logger.info("Job %s finished with status '%s'", job.id, job.status)

def find_unfinished_job_ids(session_factory: sessionmaker) -> List[str]:
    """Jobs that were still pending/processing when the server last stopped."""
    with session_factory() as db:
        return list(
            db.scalars(
                select(Job.id).where(Job.status.in_(JobStatus.UNFINISHED)).order_by(Job.created_at)
            )
        )