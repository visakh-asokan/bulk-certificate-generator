"""Shared test setup.

Every test gets its own application, built on a throwaway SQLite database and
storage folder inside pytest's `tmp_path`, so tests never share state and never
touch the real `certificates.db`.

Note: FastAPI's TestClient runs background tasks before `client.post(...)`
returns, so by the time a test looks at a job it has already been processed.
Tests that need to see a job *before* processing use `hold_background_work`.
"""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "storage",
        max_recipients_per_job=50,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(app):
    """A database session for inspecting what the API stored."""
    with app.state.session_factory() as session:
        yield session


@pytest.fixture
def payload():
    """A valid request with three recipients. Tests modify it as needed."""
    return {
        "course_name": "Introduction to Machine Learning",
        "issuer_name": "Acme Academy",
        "issue_date": "2026-10-09",
        "recipients": [
            {"name": "Asha Rao", "email": "asha.rao@example.com"},
            {"name": "Daniel Okafor", "email": "daniel.okafor@example.com"},
            {"name": "Mei Tanaka", "email": "mei.tanaka@example.com"},
        ],
    }


@pytest.fixture
def hold_background_work(monkeypatch):
    """Stop the background task from running, so a new job stays 'pending'."""
    monkeypatch.setattr("app.routes.process_job", lambda *args, **kwargs: None)


def pdf_text(pdf_bytes: bytes) -> str:
    """All the text on the first page of a PDF."""
    return PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()


@pytest.fixture
def read_pdf_text():
    return pdf_text