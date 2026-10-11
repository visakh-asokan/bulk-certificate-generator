"""Job status and progress."""
from app.worker import process_job


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_new_job_is_pending_with_zero_progress(client, payload, hold_background_work):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert status["status"] == "pending"
    assert status["total"] == 3
    assert status["counts"] == {"pending": 3, "generated": 0, "failed": 0, "invalid": 0}
    assert status["progress_percent"] == 0.0
    assert status["started_at"] is None
    assert status["completed_at"] is None


def test_finished_job_is_completed_with_full_progress(client, payload):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert status["status"] == "completed"
    assert status["counts"] == {"pending": 0, "generated": 3, "failed": 0, "invalid": 0}
    assert status["progress_percent"] == 100.0
    assert status["failures"] == []
    assert status["started_at"] is not None
    assert status["completed_at"] is not None
    assert status["completed_at"].endswith("Z")  # timestamps are explicit UTC


def test_progress_moves_from_pending_to_finished(client, app, settings, payload, hold_background_work):
    payload["recipients"].append({"name": "", "email": "blank@example.com"})  # 3 valid + 1 invalid
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    before = client.get(f"/api/v1/jobs/{job_id}").json()
    assert before["status"] == "pending"
    assert before["counts"] == {"pending": 3, "generated": 0, "failed": 0, "invalid": 1}
    assert before["progress_percent"] == 25.0  # the invalid row is already settled

    # Now do the work the background task would have done.
    process_job(job_id, app.state.session_factory, settings.storage_dir)

    after = client.get(f"/api/v1/jobs/{job_id}").json()
    assert after["status"] == "completed_with_errors"
    assert after["counts"] == {"pending": 0, "generated": 3, "failed": 0, "invalid": 1}
    assert after["progress_percent"] == 100.0


def test_status_identifies_which_rows_did_not_get_a_certificate(client, payload):
    payload["recipients"].append({"name": "Bo Li", "email": "not-an-email"})
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert len(status["failures"]) == 1
    failure = status["failures"][0]
    assert failure["row"] == 4
    assert failure["recipient_name"] == "Bo Li"
    assert failure["status"] == "invalid"
    assert "email" in failure["error"]
    assert failure["download_url"] is None