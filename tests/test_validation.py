"""Input validation: whole-request rules and row-by-row recipient rules."""
import pytest
from sqlalchemy import func, select

from app.models import Job
from app.validation import validate_recipients

# ---------- Problems with the request as a whole: nothing is created ----------


def test_missing_course_name_is_rejected(client, payload):
    del payload["course_name"]

    assert client.post("/api/v1/jobs", json=payload).status_code == 422


def test_empty_recipient_list_is_rejected(client, payload):
    payload["recipients"] = []

    assert client.post("/api/v1/jobs", json=payload).status_code == 422


def test_more_recipients_than_the_limit_is_rejected(client, payload):
    # The test settings allow at most 50 recipients per job.
    payload["recipients"] = [
        {"name": f"Person {chr(65 + i % 26)}", "email": f"person{i}@example.com"} for i in range(51)
    ]

    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 422
    assert "Too many recipients" in response.json()["detail"]


def test_course_name_the_template_cannot_draw_is_rejected(client, payload):
    payload["course_name"] = "機械学習入門"

    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 422
    assert "Course name" in response.json()["detail"]


def test_request_with_no_valid_recipient_creates_no_job(client, db, payload):
    payload["recipients"] = [{"name": "", "email": "a@example.com"}, {"name": "Bo Li", "email": "nope"}]

    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [r["row"] for r in detail["rejected_recipients"]] == [1, 2]
    assert db.scalar(select(func.count()).select_from(Job)) == 0


# ---------- Problems with individual rows: reported, others carry on ----------


def test_invalid_rows_are_reported_and_valid_rows_still_generate(client, payload):
    payload["recipients"] = [
        {"name": "Asha Rao", "email": "asha.rao@example.com"},  # row 1: fine
        {"name": "", "email": "blank.name@example.com"},  # row 2: blank name
        {"name": "Daniel Okafor", "email": "not-an-email"},  # row 3: bad email
        {"name": "Asha R.", "email": "ASHA.RAO@example.com"},  # row 4: duplicate of row 1
        "just a string",  # row 5: not an object
        {"email": "no.name@example.com"},  # row 6: name missing
        {"name": "Mei Tanaka", "email": "mei.tanaka@example.com"},  # row 7: fine
    ]

    created = client.post("/api/v1/jobs", json=payload)

    assert created.status_code == 202
    body = created.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 5
    assert [r["row"] for r in body["rejected_recipients"]] == [2, 3, 4, 5, 6]
    assert "duplicate of row 1" in body["rejected_recipients"][2]["errors"][0]

    status = client.get(body["status_url"]).json()
    assert status["counts"] == {"pending": 0, "generated": 2, "failed": 0, "invalid": 5}
    assert status["status"] == "completed_with_errors"


def test_valid_rows_are_cleaned_up():
    results = validate_recipients([{"name": "  Asha    Rao ", "email": " asha.rao@example.com "}])

    assert results[0].is_valid
    assert results[0].name == "Asha Rao"
    assert results[0].email == "asha.rao@example.com"


@pytest.mark.parametrize(
    "recipient, expected_error",
    [
        ({"name": "A", "email": "a@example.com"}, "at least 2 characters"),
        ({"name": "X" * 81, "email": "a@example.com"}, "at most 80 characters"),
        ({"name": "12345", "email": "a@example.com"}, "at least one letter"),
        ({"name": "विशाख", "email": "a@example.com"}, "cannot render"),
        ({"name": 42, "email": "a@example.com"}, "name:"),
        ({"name": "Asha Rao"}, "email: Field required"),
        ({"name": "Asha Rao", "email": "asha@"}, "email:"),
        (None, "must be an object"),
    ],
)
def test_bad_rows_are_rejected_with_a_reason(recipient, expected_error):
    result = validate_recipients([recipient])[0]

    assert not result.is_valid
    assert expected_error in " ".join(result.errors)


def test_every_row_gets_a_result_in_the_original_order():
    results = validate_recipients(
        [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "", "email": "bad"},
            {"name": "Mei Tanaka", "email": "mei@example.com"},
        ]
    )

    assert [(r.row, r.is_valid) for r in results] == [(1, True), (2, False), (3, True)]
    # A row with two problems reports both.
    assert len(results[1].errors) == 2