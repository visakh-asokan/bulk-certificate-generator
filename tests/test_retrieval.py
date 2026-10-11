"""Retrieving generated certificates: listing, single PDF, ZIP of the whole job."""
import zipfile
from io import BytesIO

import app.worker as worker


def create_finished_job(client, payload):
    return client.post("/api/v1/jobs", json=payload).json()["job_id"]


def test_listing_shows_every_recipient_with_a_download_link(client, payload):
    job_id = create_finished_job(client, payload)

    page = client.get(f"/api/v1/jobs/{job_id}/certificates").json()

    assert page["total"] == 3
    assert [item["recipient_name"] for item in page["items"]] == ["Asha Rao", "Daniel Okafor", "Mei Tanaka"]
    for item in page["items"]:
        assert item["status"] == "generated"
        assert item["download_url"] == f"/api/v1/certificates/{item['certificate_id']}/download"


def test_listing_can_be_filtered_by_status(client, payload):
    payload["recipients"].append({"name": "Bo Li", "email": "not-an-email"})
    job_id = create_finished_job(client, payload)

    generated = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "generated"}).json()
    invalid = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "invalid"}).json()

    assert generated["total"] == 3
    assert invalid["total"] == 1
    assert invalid["items"][0]["recipient_name"] == "Bo Li"
    assert client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "bogus"}).status_code == 422


def test_listing_is_paginated(client, payload):
    job_id = create_finished_job(client, payload)

    page = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2}).json()

    assert page["total"] == 3  # total ignores pagination
    assert [item["row"] for item in page["items"]] == [3]


def test_listing_for_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist/certificates").status_code == 404


def test_a_generated_certificate_can_be_downloaded_as_pdf(client, payload, read_pdf_text):
    job_id = create_finished_job(client, payload)
    first = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]

    response = client.get(first["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="certificate_Asha_Rao.pdf"' in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")
    assert "Asha Rao" in read_pdf_text(response.content)


def test_downloading_a_certificate_that_failed_explains_why(client, payload, monkeypatch):
    def always_fail(**details):
        raise RuntimeError("template is broken")

    monkeypatch.setattr(worker, "render_certificate", always_fail)
    job_id = create_finished_job(client, payload)
    failed = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]

    response = client.get(f"/api/v1/certificates/{failed['certificate_id']}/download")

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "failed"
    assert "template is broken" in response.json()["detail"]["error"]


def test_downloading_an_unknown_certificate_returns_404(client):
    assert client.get("/api/v1/certificates/does-not-exist/download").status_code == 404


def test_all_certificates_of_a_job_can_be_downloaded_as_one_zip(client, payload):
    payload["recipients"].append({"name": "Bo Li", "email": "not-an-email"})  # must not appear in the ZIP
    job_id = create_finished_job(client, payload)

    response = client.get(f"/api/v1/jobs/{job_id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "00001_Asha_Rao.pdf",
            "00002_Daniel_Okafor.pdf",
            "00003_Mei_Tanaka.pdf",
        ]
        assert archive.read("00001_Asha_Rao.pdf").startswith(b"%PDF")


def test_zip_is_refused_while_the_job_is_still_running(client, payload, hold_background_work):
    job_id = create_finished_job(client, payload)  # stays pending: background work is held

    assert client.get(f"/api/v1/jobs/{job_id}/download").status_code == 409