"""Creating a generation job."""
from datetime import date

from sqlalchemy import select

from app.models import Certificate, Job


def test_create_job_returns_202_with_a_job_id(client, payload):
    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["total_recipients"] == 3
    assert body["accepted"] == 3
    assert body["rejected"] == 0
    assert body["rejected_recipients"] == []
    assert body["status_url"] == f"/api/v1/jobs/{body['job_id']}"
    assert body["certificates_url"] == f"/api/v1/jobs/{body['job_id']}/certificates"


def test_create_job_stores_the_job_and_one_row_per_recipient(client, db, payload, hold_background_work):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    job = db.get(Job, job_id)
    assert job.course_name == "Introduction to Machine Learning"
    assert job.issuer_name == "Acme Academy"
    assert job.issue_date == date(2026, 10, 9)
    assert job.status == "pending"

    rows = db.scalars(
        select(Certificate).where(Certificate.job_id == job_id).order_by(Certificate.row_number)
    ).all()
    assert [(row.row_number, row.recipient_name, row.status) for row in rows] == [
        (1, "Asha Rao", "pending"),
        (2, "Daniel Okafor", "pending"),
        (3, "Mei Tanaka", "pending"),
    ]


def test_create_job_hands_the_job_to_the_background_worker(client, payload, monkeypatch):
    queued_job_ids = []
    monkeypatch.setattr(
        "app.routes.process_job", lambda job_id, *args: queued_job_ids.append(job_id)
    )

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    assert queued_job_ids == [job_id]


def test_issue_date_defaults_to_today(client, db, payload):
    del payload["issue_date"]

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    assert db.get(Job, job_id).issue_date == date.today()


def test_many_recipients_are_accepted_in_a_single_request(client, payload):
    payload["recipients"] = [
        {"name": f"Participant {chr(65 + i % 26)}{i}", "email": f"participant{i}@example.com"}
        for i in range(50)
    ]

    created = client.post("/api/v1/jobs", json=payload).json()
    status = client.get(created["status_url"]).json()

    assert created["accepted"] == 50
    assert status["status"] == "completed"
    assert status["counts"]["generated"] == 50