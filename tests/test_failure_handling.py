"""One certificate failing must not stop the rest of the job."""
import time

from fastapi.testclient import TestClient

import app.worker as worker
from app.main import create_app


def fail_for(monkeypatch, name):
    """Make the template raise for one recipient and behave normally for the rest."""
    real_render = worker.render_certificate

    def flaky_render(**details):
        if details["recipient_name"] == name:
            raise RuntimeError("simulated rendering crash")
        return real_render(**details)

    monkeypatch.setattr(worker, "render_certificate", flaky_render)


def test_one_failing_certificate_does_not_stop_the_others(client, payload, monkeypatch):
    fail_for(monkeypatch, "Daniel Okafor")  # row 2 of 3

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert status["status"] == "completed_with_errors"
    assert status["counts"] == {"pending": 0, "generated": 2, "failed": 1, "invalid": 0}

    # The rows before AND after the failure were both generated.
    results = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    assert [(r["row"], r["status"]) for r in results] == [
        (1, "generated"),
        (2, "failed"),
        (3, "generated"),
    ]


def test_the_failure_is_recorded_with_its_reason(client, payload, monkeypatch):
    fail_for(monkeypatch, "Daniel Okafor")

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    failures = client.get(f"/api/v1/jobs/{job_id}").json()["failures"]

    assert len(failures) == 1
    assert failures[0]["recipient_name"] == "Daniel Okafor"
    assert failures[0]["status"] == "failed"
    assert "simulated rendering crash" in failures[0]["error"]
    assert failures[0]["download_url"] is None


def test_a_storage_error_for_one_certificate_is_isolated_too(client, payload, monkeypatch):
    real_save = worker.save_pdf
    calls = []

    def save_that_fails_once(storage_dir, relative_path, content):
        calls.append(relative_path)
        if len(calls) == 1:
            raise OSError("No space left on device")
        real_save(storage_dir, relative_path, content)

    monkeypatch.setattr(worker, "save_pdf", save_that_fails_once)

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert status["counts"]["failed"] == 1
    assert status["counts"]["generated"] == 2
    assert "No space left on device" in status["failures"][0]["error"]


def test_a_real_template_failure_is_isolated_without_any_mocking(client, payload):
    # 80 wide letters pass validation (length is fine) but cannot fit on the page.
    payload["recipients"].append({"name": "W" * 80, "email": "wide@example.com"})

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert status["status"] == "completed_with_errors"
    assert status["counts"] == {"pending": 0, "generated": 3, "failed": 1, "invalid": 0}
    assert "too long to fit" in status["failures"][0]["error"]


def test_job_is_failed_when_no_certificate_could_be_generated(client, payload, monkeypatch):
    def always_fail(**details):
        raise RuntimeError("template is broken")

    monkeypatch.setattr(worker, "render_certificate", always_fail)

    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    status = client.get(f"/api/v1/jobs/{job_id}").json()

    assert status["status"] == "failed"
    assert status["counts"] == {"pending": 0, "generated": 0, "failed": 3, "invalid": 0}


def test_a_job_interrupted_by_a_restart_is_resumed_on_startup(settings, payload, hold_background_work):
    # First "server run": the job is accepted but the process stops before any work is done.
    with TestClient(create_app(settings)) as first_run:
        job_id = first_run.post("/api/v1/jobs", json=payload).json()["job_id"]
        assert first_run.get(f"/api/v1/jobs/{job_id}").json()["status"] == "pending"

    # Second "server run" on the same database: startup picks the job up again.
    with TestClient(create_app(settings)) as second_run:
        status = {}
        for _ in range(100):  # the resumed job runs in a thread; wait up to 5s
            status = second_run.get(f"/api/v1/jobs/{job_id}").json()
            if status["status"] == "completed":
                break
            time.sleep(0.05)

    assert status["status"] == "completed"
    assert status["counts"]["generated"] == 3