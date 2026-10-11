"""The single predefined certificate template.

`render_certificate` is a pure function: certificate details in, PDF bytes out.
It never touches the database or the disk, which keeps it easy to test and
means the template could be replaced (say, with an HTML-to-PDF renderer)
without changing any caller.
"""
from datetime import date
from io import BytesIO
from typing import List

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)  # 842 x 595 points, origin at bottom-left
CENTRE = PAGE_WIDTH / 2
BODY_WIDTH = PAGE_WIDTH - 220  # widest a line of body text may be
FOOTER_WIDTH = 260  # widest a footer value may be

NAVY = HexColor("#1B2A49")
GOLD = HexColor("#B8893B")
GREY = HexColor("#5F6B7A")


class CertificateRenderError(Exception):
    """The template cannot produce a correct certificate from this data."""

def unsupported_characters(text: str) -> List[str]:
    """Return the characters in `text` that the template's fonts cannot draw.

    The template uses the fonts built into every PDF reader (Times, Helvetica).
    They only cover Latin text (the cp1252 character set). ReportLab does not
    raise for anything else, it silently draws the wrong glyphs, so we check
    explicitly rather than hand out a certificate with a garbled name.
    """
    unsupported: List[str] = []
    for character in text:
        try:
            character.encode("cp1252")
            drawable = character.isprintable()
        except UnicodeEncodeError:
            drawable = False
        if not drawable and character not in unsupported:
            unsupported.append(character)
    return unsupported

def _fitted_font_size(text: str, font: str, largest: int, smallest: int, max_width: float) -> int:
    """Largest font size (within limits) at which `text` fits in `max_width`."""
    size = largest
    while stringWidth(text, font, size) > max_width:
        size -= 1
        if size < smallest:
            raise CertificateRenderError(
                f"Text is too long to fit on the certificate: '{text[:40]}...'"
            )
    return size

def render_certificate(
    *,
    recipient_name: str,
    course_name: str,
    issuer_name: str,
    issue_date: date,
    certificate_id: str,
) -> bytes:
    """Draw one certificate and return it as PDF bytes."""
    for label, value in (
        ("Recipient name", recipient_name),
        ("Course name", course_name),
        ("Issuer name", issuer_name),
    ):
        unsupported = unsupported_characters(value)
        if unsupported:
            raise CertificateRenderError(
                f"{label} contains characters the certificate font cannot render: "
                + " ".join(unsupported[:5])
            )

    # Work out sizes first so that a text-fit error is raised before drawing.
    name_size = _fitted_font_size(recipient_name, "Times-BoldItalic", 40, 16, BODY_WIDTH)
    course_size = _fitted_font_size(course_name, "Helvetica-Bold", 24, 12, BODY_WIDTH)
    issuer_size = _fitted_font_size(issuer_name, "Helvetica-Bold", 13, 8, FOOTER_WIDTH)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle(f"Certificate of Completion - {recipient_name}")
    pdf.setAuthor(issuer_name)
    pdf.setSubject(course_name)

    # Double border
    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(3)
    pdf.rect(28, 28, PAGE_WIDTH - 56, PAGE_HEIGHT - 56)
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(1)
    pdf.rect(38, 38, PAGE_WIDTH - 76, PAGE_HEIGHT - 76)

    # Heading
    pdf.setFillColor(NAVY)
    pdf.setFont("Times-Bold", 46)
    pdf.drawCentredString(CENTRE, 468, "CERTIFICATE")
    pdf.setFillColor(GOLD)
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(CENTRE, 438, "OF COMPLETION")
    # Recipient
    pdf.setFillColor(GREY)
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(CENTRE, 385, "This is to certify that")
    pdf.setFillColor(NAVY)
    pdf.setFont("Times-BoldItalic", name_size)
    pdf.drawCentredString(CENTRE, 335, recipient_name)
    underline = max(stringWidth(recipient_name, "Times-BoldItalic", name_size) / 2 + 30, 150)
    pdf.setStrokeColor(GOLD)
    pdf.line(CENTRE - underline, 318, CENTRE + underline, 318)

    # Course
    pdf.setFillColor(GREY)
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(CENTRE, 285, "has successfully completed")
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", course_size)
    pdf.drawCentredString(CENTRE, 245, course_name)

    # Footer: date on the left, issuer on the right
    formatted_date = f"{issue_date.day} {issue_date.strftime('%B %Y')}"
    for x, value, size, label in (
        (200, formatted_date, 13, "Date of issue"),
        (PAGE_WIDTH - 200, issuer_name, issuer_size, "Issued by"),
    ):
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold", size)
        pdf.drawCentredString(x, 130, value)
        pdf.setStrokeColor(GREY)
        pdf.line(x - 110, 120, x + 110, 120)
        pdf.setFillColor(GREY)
        pdf.setFont("Helvetica", 10)
        pdf.drawCentredString(x, 104, label)

    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(CENTRE, 60, f"Certificate ID: {certificate_id}")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
