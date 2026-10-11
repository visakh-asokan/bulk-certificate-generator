# Bulk Certificate Generator

A backend API that takes a list of recipients in one request, generates a PDF
certificate for each valid recipient from a predefined template, and lets the
client track progress and download the results.

- **Stack:** Python, FastAPI, SQLAlchemy, SQLite, ReportLab (PDF)
- **Processing:** asynchronous. The request returns a job id straight away and the
  certificates are generated in the background.

```
POST /api/v1/jobs ──► validate rows ──► save job + one row per recipient ──► 202 + job_id
                                                   │
                                  background task  ▼
                             for each pending row: render PDF ─► save file ─► mark generated
                                                   (on error: mark that row failed, carry on)

GET /api/v1/jobs/{id}                     progress and failures
GET /api/v1/jobs/{id}/certificates        per-recipient results with download links
GET /api/v1/certificates/{id}/download    one PDF
GET /api/v1/jobs/{id}/download            all PDFs as a ZIP
```

## 1. Setup

Requires Python 3.9 or newer. Developed and tested on Python 3.12 (macOS). No
database server is needed; SQLite is a file.

```bash
git clone <repository-url>
cd bulk-certificate-generator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install --no-cache-dir -r requirements-dev.txt
```

`requirements-dev.txt` installs the application and the test tools.
`requirements.txt` alone is enough to run the application. Everything is
installed inside `.venv/`; deleting that folder removes it all.

## 2. Run the application

```bash
uvicorn app.main:create_app --factory --reload --reload-dir app
```

- API: http://127.0.0.1:8000
- Interactive documentation (try requests from the browser): http://127.0.0.1:8000/docs

On first start the app creates `certificates.db` and, once certificates are
generated, a `storage/` folder, both in the project folder.

Optional environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL for a relational database |
| `STORAGE_DIR` | `storage` | Folder where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `10000` | Largest list accepted in one request |

## 3. Run the tests

```bash
pytest
```

47 tests, about one second. Each test runs against its own temporary database
and storage folder, so tests never touch real data.

| File | What it covers |
| --- | --- |
| `tests/test_create_job.py` | Creating a generation job |
| `tests/test_validation.py` | Input validation (whole request and row by row) |
| `tests/test_generation.py` | Certificate generation (template and files written) |
| `tests/test_job_status.py` | Job status and progress |
| `tests/test_failure_handling.py` | One certificate failing, and resuming after a restart |
| `tests/test_retrieval.py` | Listing and downloading certificates |

## 4. Submit a certificate generation request

`sample_request.json` holds three valid recipients and two invalid ones:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Request body:

| Field | Required | Rules |
| --- | --- | --- |
| `course_name` | yes | 2 to 120 characters |
| `issuer_name` | yes | 2 to 80 characters |
| `issue_date` | no | `YYYY-MM-DD`, defaults to today |
| `recipients` | yes | 1 to 10,000 objects of `{"name": ..., "email": ...}` |

Response, `202 Accepted`:

```json
{
  "job_id": "963ce5db-847f-49ad-967c-1167a0647f36",
  "status": "pending",
  "total_recipients": 5,
  "accepted": 3,
  "rejected": 2,
  "rejected_recipients": [
    {"row": 4, "name": "", "email": "blank.name@example.com",
     "errors": ["name: must be at least 2 characters"]},
    {"row": 5, "name": "Lucas Meyer", "email": "not-an-email",
     "errors": ["email: value is not a valid email address: An email address must have an @-sign."]}
  ],
  "status_url": "/api/v1/jobs/963ce5db-847f-49ad-967c-1167a0647f36",
  "certificates_url": "/api/v1/jobs/963ce5db-847f-49ad-967c-1167a0647f36/certificates"
}
```

The status is `pending` because the reply is sent before generation starts.

To try a large job, build a request with 5,000 recipients and send it:

```bash
python -c "
import json

recipients = [
    {'name': f'Participant {chr(65 + i % 26)}', 'email': f'participant{i}@example.com'}
    for i in range(5000)
]
request = {'course_name': 'Big Course', 'issuer_name': 'My Academy', 'recipients': recipients}
json.dump(request, open('big_request.json', 'w'))
"
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @big_request.json
```

## 5. Check progress

```bash
curl -s http://127.0.0.1:8000/api/v1/jobs/<job_id> | python -m json.tool
```

```json
{
  "job_id": "963ce5db-847f-49ad-967c-1167a0647f36",
  "status": "completed_with_errors",
  "total": 5,
  "counts": {"pending": 0, "generated": 3, "failed": 0, "invalid": 2},
  "progress_percent": 100.0,
  "failures": [
    {"certificate_id": "729c2734-...", "row": 4, "recipient_name": "",
     "recipient_email": "blank.name@example.com", "status": "invalid",
     "error": "name: must be at least 2 characters", "download_url": null, "generated_at": null}
  ],
  "certificates_url": "/api/v1/jobs/963ce5db-.../certificates",
  "download_url": "/api/v1/jobs/963ce5db-.../download"
}
```

(Shortened: the real response also has the course details and timestamps, and
lists both failures.)

Job statuses:

| Status | Meaning |
| --- | --- |
| `pending` | Accepted, generation not started yet |
| `processing` | Certificates are being generated; watch `counts` and `progress_percent` |
| `completed` | Every recipient got a certificate |
| `completed_with_errors` | Some recipients got one, some did not; see `failures` |
| `failed` | No certificate could be generated |

Certificate statuses: `pending`, `generated`, `failed` (generation was attempted
and raised an error), `invalid` (rejected by validation, never attempted).

`failures` lists the first 100 rows that did not get a certificate. The full
list is available from the certificates endpoint with `?status=failed` or
`?status=invalid`.

## 6. Retrieve generated certificates

List the per-recipient results (paginated, 100 per page by default):

```bash
curl -s "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates" | python -m json.tool
curl -s "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=generated&limit=50&offset=0"
```

Each generated item has a `download_url`. Download one PDF:

```bash
curl -o certificate.pdf http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
```

Download every generated certificate of a job as one ZIP (available once the
job has finished):

```bash
curl -o certificates.zip http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
```

| Situation | Response |
| --- | --- |
| Unknown job or certificate id | `404` |
| Certificate exists but was not generated (`failed`, `invalid`, `pending`) | `409` with its status and error |
| ZIP requested while the job is still running | `409` |

## 7. Design decisions

### Background processing, not synchronous

A request can contain thousands of recipients. Generating them inside the
request would keep the connection open for the whole run, risk client and proxy
timeouts, show no progress, and lose everything if the connection dropped. So
`POST /jobs` only validates and stores the job, returns `202 Accepted` with a
job id, and the client polls the status endpoint.

The background work uses FastAPI's built-in `BackgroundTasks`, which runs the
job in a worker thread of the same process after the response is sent.

- **Why not Celery or RQ:** they need a message broker (Redis or RabbitMQ) and a
  separate worker process. That is the right choice in production but adds
  infrastructure that this assignment does not need.
- **What it costs:** work lives in the API process. It does not survive a
  restart on its own, and it cannot be spread over several machines.
- **How the cost is reduced:** all job state is in the database, never only in
  memory, and the worker only picks up certificates that are still `pending`.
  On startup the app finds unfinished jobs and resumes them, so a restart delays
  a job instead of losing it.
- **Path to a task queue:** `process_job` needs only a job id from the request
  and reads everything else from the database. Moving to Celery means replacing
  the `background_tasks.add_task(process_job, ...)` line with a call that sends
  the job id to the queue.

Measured on a MacBook Air: a job of 5,000 recipients was generated in 3.8
seconds, about 1,300 certificates per second.

### Two levels of validation

- **Whole request** (missing course name, empty list, more than the maximum,
  course or issuer name the template cannot draw): rejected with `422`, nothing
  is created. These problems would affect every certificate.
- **Individual rows** (blank or too long name, invalid email, duplicate email,
  not an object): the row is recorded as `invalid` with its reasons and reported
  in the response; the valid rows go ahead. This is why `recipients` is typed as
  a loose list and validated row by row. With a strict type, FastAPI would
  reject the whole request because of a single bad row.

If no row is valid, the request is rejected with `422` and the per-row reasons,
and no job is created, since there would be nothing to generate.

A duplicate email inside one request is treated as invalid (the first
occurrence wins), because it is almost always a copy-paste mistake and would
otherwise issue two certificates to one person.

### One failure does not stop the job

Each certificate is generated inside its own `try/except` (`_generate_one` in
`app/worker.py`). Any exception is recorded on that certificate's row (`failed`
plus the error text) and the loop continues. The result is committed after
every certificate, so progress is visible immediately and a crash loses at most
the one certificate in flight. PDFs are written to a temporary file and then
renamed, so a half-written file can never look finished.

### One row per recipient; counts are computed, not stored

The `certificates` table has a row for every recipient in the request,
including invalid ones, with `row_number` matching the position in the
client's list. The job is therefore a complete record of what was requested and
what happened to each row. Progress counts come from a `GROUP BY status` query
over those rows (indexed on `job_id, status`) instead of counters stored on the
job, so the numbers cannot drift from reality.

### Template

A single template drawn with ReportLab on landscape A4 (`app/generator.py`).
`render_certificate` is a pure function, details in and PDF bytes out, with no
database or disk access, so it is easy to test and to replace. Long names are
shrunk to fit. ReportLab was chosen because it is pure Python with one small
dependency, and needs no browser or system libraries (unlike HTML-to-PDF tools).

### Storage and identifiers

PDFs are stored on local disk under `storage/certificates/<job_id>/`; the
database stores only the relative path. Job and certificate ids are random
UUIDs, so download URLs cannot be guessed by counting upwards. This matters
because the API has no authentication.

### Database

SQLite through SQLAlchemy: a relational database with no server to install.
It runs in WAL mode so status requests can read while the background job
writes. Nothing is SQLite-specific beyond the connection setup in
`app/database.py`; setting `DATABASE_URL` to a PostgreSQL URL (plus installing
a driver) is the only change needed.

### Application factory

`create_app(settings)` builds the app from a `Settings` object. Tests create an
app per test with a temporary database and storage folder, which keeps them
isolated without patching global state.

## 8. Limitations and next steps

- **Latin text only.** The template uses the fonts built into PDF readers,
  which cover Latin characters (including accents such as é, ñ, ü). Names in
  other scripts are rejected at validation with a clear reason, rather than
  printed as wrong glyphs. Supporting them needs a bundled Unicode font, and a
  text-shaping library for scripts such as Devanagari.
- **Single process.** Run one server process. With several, each would try to
  resume the same unfinished job on startup. A task queue removes this limit.
- **No authentication.** Anyone with a certificate id can download it.
- **No retry endpoint.** A `failed` certificate stays failed; a
  `POST /jobs/{id}/retry` that sets failed rows back to `pending` and queues
  `process_job` again would be a small addition, since the worker already
  processes only pending rows.
- **Local file storage.** For more than one server, PDFs would move to object
  storage such as S3 behind the same `save_pdf` function.
- **Schema changes.** Tables are created with `create_all`; a real deployment
  would use Alembic migrations.

## 9. Project layout

```
app/
  main.py         application factory, startup resume
  config.py       settings from environment variables
  database.py     engine and session setup
  models.py       Job and Certificate tables, status values
  schemas.py      request and response shapes
  validation.py   row-by-row recipient validation
  generator.py    the certificate template (details in, PDF bytes out)
  worker.py       background processing of a job
  routes.py       the HTTP endpoints
tests/            one file per area listed in section 3
sample_request.json   small sample request
```
