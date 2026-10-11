"""Certificate generation: the template itself, and writing PDFs for a job."""
from datetime import date

import pytest
from sqlalchemy import select

from app.generator import CertificateRenderError, render_certificate, unsupported_characters
from app.models import Certificate

DETAILS = dict(
    recipient_name="Asha Rao",
    course_name="Introduction to Machine Learning",
    issuer_name="Acme Academy",
    issue_date=date(2026, 10, 9),
    certificate_id="test-certificate-id",
)


def test_certificate_is_a_pdf_containing_the_recipient_details(read_pdf_text):
    pdf_bytes = render_certificate(**DETAILS)

    assert pdf_bytes.startswith(b"%PDF")
    text = read_pdf_text(pdf_bytes)
    assert "Asha Rao" in text
    assert "Introduction to Machine Learning" in text
    assert "Acme Academy" in text
    assert "9 October 2026" in text
    assert "test-certificate-id" in text


def test_each_recipient_gets_their_own_name(read_pdf_text):
    other = render_certificate(**{**DETAILS, "recipient_name": "Daniel Okafor"})

    text = read_pdf_text(other)
    assert "Daniel Okafor" in text
    assert "Asha Rao" not in text


def test_a_long_name_is_shrunk_to_fit(read_pdf_text):
    long_name = "Maximilian Alexander Bartholomew Fitzgerald-Montgomery"

    pdf_bytes = render_certificate(**{**DETAILS, "recipient_name": long_name})

    assert long_name in read_pdf_text(pdf_bytes)


def test_a_name_too_long_for_the_template_raises():
    with pytest.raises(CertificateRenderError, match="too long"):
        render_certificate(**{**DETAILS, "recipient_name": "W" * 80})


def test_characters_the_font_cannot_draw_raise():
    assert unsupported_characters("José Müller") == []
    assert unsupported_characters("विशाख") != []

    with pytest.raises(CertificateRenderError, match="cannot render"):
        render_certificate(**{**DETAILS, "recipient_name": "विशाख"})


def test_processing_a_job_writes_one_pdf_per_recipient(client, db, settings, payload, read_pdf_text):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    certificates = db.scalars(select(Certificate).where(Certificate.job_id == job_id)).all()
    assert len(certificates) == 3
    for certificate in certificates:
        assert certificate.status == "generated"
        assert certificate.generated_at is not None
        pdf_path = settings.storage_dir / certificate.file_path
        assert pdf_path.is_file()
        text = read_pdf_text(pdf_path.read_bytes())
        assert certificate.recipient_name in text
        assert certificate.id in text  # the certificate carries its own id