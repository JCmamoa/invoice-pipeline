# Invoice Pipeline

**Automated invoice intake for finance teams.** A PDF lands in a folder, arrives
by e-mail or is uploaded; the pipeline extracts **CNPJ, total amount and due
date**, validates them deterministically, stores the result in Postgres with an
audit trail, archives the original and notifies the person responsible.

The design principle is simple: **never invent data**. When a value is missing or
invalid, the invoice goes to human review with the exact reason instead of
guessing.

[![CI](https://github.com/JCmamoa/invoice-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/JCmamoa/invoice-pipeline/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![Postgres](https://img.shields.io/badge/Postgres-16-336791)
![License](https://img.shields.io/badge/license-MIT-green)

## Try it now

| Tool | Link | What runs there |
|---|---|---|
| **Public browser demo** | **<https://jcmamoa.github.io/invoice-pipeline-demo/>** | Extraction + validation in your browser (pdf.js). No upload, no backend. |
| Full stack (local Docker) | <http://localhost:8000> | API, worker, Postgres, OCR, audit trail, notifications |
| API reference | <http://localhost:8000/docs> | OpenAPI / Swagger UI |
| Demo source (public) | <https://github.com/JCmamoa/invoice-pipeline-demo> | Static demo repository |

![Browser demo walkthrough](docs/media/demo.gif)

The public demo runs the same extraction and validation rules in JavaScript.
The full pipeline (queue, audit trail, OCR, notifications) runs in Docker:

![Invoice pipeline UI](docs/media/overview.jpg)

![Invoice detail with OCR source and audit trail](docs/media/detail-ocr.jpg)

---

## The problem

Finance teams still copy invoice fields by hand. For every invoice someone
opens the PDF, finds the supplier, types the CNPJ into the ERP, copies the
total, reads the due date and sends a message to the manager. It is slow,
repetitive and error-prone:

- **Time**: 4–6 minutes per invoice, every month, for every supplier.
- **Errors**: a transposed CNPJ digit or a misread `1.234,56` becomes a wrong
  payment. Manual entry has no checksum.
- **No trace**: when a value is wrong, there is no record of who read what, when,
  or from which version of the document.
- **Duplicates**: the same invoice arrives by e-mail and by upload and is entered
  twice.
- **Scans**: many suppliers send image-only PDFs that a simple text parser
  cannot read.

## The solution

| Stage | What happens | Guarantee |
|---|---|---|
| **Trigger** | upload API, watched folder, optional IMAP mailbox | the same flow for every channel |
| **Ingest** | PDF magic/size check, SHA-256, original archived | duplicates rejected, source preserved |
| **Extract** | strict JSON prompt (LLM) or deterministic parser | only values present in the document |
| **OCR** | Tesseract fallback for image-only PDFs | scans handled locally, no external service |
| **Validate** | CNPJ checksum, money/date parsing, confidence floor | invalid data never reaches the ERP |
| **Store** | Postgres row + `audit_log` + notification history | every transition is traceable |
| **Notify** | Slack Block Kit / Teams MessageCard / mock feed | the manager knows immediately |

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        A1[Upload API]
        A2[Inbox folder]
        A3[IMAP mailbox]
    end

    A1 --> B[FastAPI intake]
    A2 --> W1[Folder watcher]
    A3 --> W2[IMAP poller]
    W1 --> B
    W2 --> B

    B -->|SHA-256 dedup| DB[(Postgres)]
    B -->|original PDF| AR[(Archive volume)]

    DB -->|FOR UPDATE SKIP LOCKED| WK[Worker]
    WK --> T{Text layer?}
    T -->|yes| X[Extractor<br/>LLM or mock]
    T -->|no| O[OCR<br/>pypdfium2 + Tesseract]
    O --> X
    X --> V[Validator<br/>CNPJ · money · date]
    V -->|valid| P[processed]
    V -->|missing or invalid| R[needs_review]
    P --> N[Notifier]
    N --> S[Slack]
    N --> M[Teams]
    N --> F[Mock feed / UI]
```

| Component | Responsibility |
|---|---|
| `api` | upload, listing, detail, archive download, demo controls, web UI, `/metrics` |
| `worker` | folder watcher, queue consumer, stale-claim reaper, optional IMAP poller |
| `postgres` | invoices (business data **and** queue), audit log, notifications |
| `data` volume | `inbox/` drop zone and `archive/` original PDFs |

The API and the worker never call each other: they share Postgres. The worker can
be restarted or scaled independently, and an API outage does not stop processing.

### Invoice lifecycle

```
queued ──claim──▶ processing ──▶ processed   (all fields valid, notification sent)
                      │
                      ├────────▶ needs_review (missing/invalid field, no OCR text)
                      └────────▶ failed       (unreadable PDF, extraction error)
```

- Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`, so parallel workers never
  take the same invoice.
- Transient LLM errors are retried after 10s/30s/60s.
- A reaper requeues `processing` rows older than `STALE_PROCESSING_SECONDS`, so a
  crashed worker does not leave an invoice stuck.
- The original PDF is archived **before** enqueueing, so it is never lost.

---

## Impact

### Measured on the demo

Numbers from the local stack (`docker compose`), 5 sample invoices, worker poll
interval 2s:

| Metric | Measured |
|---|---|
| End-to-end, digital invoice | **2.03 – 2.18 s** (includes the 2s queue poll) |
| End-to-end, scanned invoice (OCR) | **4.03 s** |
| Extraction + validation (core logic, mock extractor) | **~3.5 ms** per invoice |
| Invoices processed / routed to review | 3 / 2 (the samples are seeded to exercise both paths) |
| Fabricated fields | 0 (missing values are `NULL` + reason) |

Reproduce with:

```bash
curl -s localhost:8000/api/stats
curl -s localhost:8001/metrics | grep invoice_extraction_duration
```

### Modelled savings

This is a **model with explicit assumptions**, not a measurement of your team:

- Manual entry: **5 minutes per invoice** (open PDF, find fields, type into ERP,
  notify).
- Exceptions: **15%** of invoices need review, at **3 minutes** each.
- Monitoring: **30 minutes per month** for the whole pipeline.

| Invoices / month | Manual | Automated (review + monitoring) | **Hours saved / month** | Saved / year |
|---|---|---|---|---|
| 100 | 8.3 h | 1.2 h | **7.1 h** (85%) | ~85 h |
| 500 | 41.7 h | 4.3 h | **37.4 h** (90%) | ~449 h |
| 2,000 | 166.7 h | 15.5 h | **151.2 h** (91%) | ~1,814 h |

At an illustrative cost of R$ 60/hour, that is roughly **R$ 425**, **R$ 2,245**
and **R$ 9,070** per month. Recalculate for your volume:

```
hours_saved = V × 5/60 − V × 0.15 × 3/60 − 0.5
```

Beyond time, the pipeline removes a class of errors: the CNPJ check digit is
verified, money is parsed in both `1.234,56` and `1,234.56`, the due date must be
plausible, and a file is never processed twice. Every decision is written to
`audit_log` with the extraction JSON.

---

## How extraction works

The LLM receives a fixed system prompt and must return exactly:

```json
{
  "supplier_name": "string|null",
  "supplier_cnpj": "string|null",
  "total_amount": "string|null",
  "currency": "BRL|USD|EUR|GBP|null",
  "due_date": "YYYY-MM-DD|null",
  "confidence": 0.0,
  "notes": "string|null"
}
```

Rules enforced by the prompt and by code:

- Use only values explicitly present in the document.
- Missing values must be `null`; the model must not infer or guess.
- The response is parsed with Pydantic and then **validated deterministically**,
  independently of the model.
- Invalid JSON is a transient error (retried); a valid response with missing
  fields becomes `needs_review`.

`LLM_MODE=mock` uses a deterministic regex extractor with the same contract, so
the demo and tests run offline and for free. Point `LLM_MODE=openai` at any
OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, vLLM).

### Validation rules

| Field | Rule | Error |
|---|---|---|
| `supplier_name` | non-empty | `missing_supplier_name` |
| `supplier_cnpj` | 14 digits + checksum | `missing_cnpj`, `invalid_cnpj` |
| `total_amount` | parses and > 0 | `missing_total`, `invalid_total` |
| `due_date` | parses, 2000-01-01 … today + 10 years | `missing_due_date`, `implausible_due_date` |
| `confidence` | `>= MIN_CONFIDENCE` (0.55) | `low_confidence` |

Missing values are stored as `NULL`; never as placeholders or zero.

### OCR fallback

Scanned PDFs have no text layer. When `OCR_ENABLED=true` the worker renders up to
`OCR_MAX_PAGES` pages with `pypdfium2` and runs a **local Tesseract**
(`eng` + `por` installed in the image). The invoice records where the text came
from in `text_source` (`text_layer`, `ocr` or `none`). If OCR is disabled,
unavailable or reads too little text, the invoice goes to `needs_review` with
`no_text_layer`.

---

## Quick start

Prerequisites: Docker with Compose v2. No Python on the host is required.

```bash
git clone https://github.com/JCmamoa/invoice-pipeline.git
cd invoice-pipeline
cp .env.example .env          # optional: set LLM key/webhooks
docker compose up -d --build
curl -sf http://localhost:8000/readyz
```

Open <http://localhost:8000> and click **Load sample invoices**. The worker picks
them up in a few seconds:

| Sample | Result |
|---|---|
| `acme_invoice.pdf` | `processed` — BRL 1,234.56 |
| `globex_invoice.pdf` | `processed` — USD 4,250.00, Portuguese labels |
| `missing_due_date.pdf` | `needs_review` — `missing_due_date` |
| `invalid_cnpj.pdf` | `needs_review` — `invalid_cnpj` |
| `scanned_invoice.pdf` | `processed` via OCR (`text_source=ocr`) |

Ports: `8000` API/UI, `8001` worker metrics, `5434` Postgres on the host.

### Test the public demo

1. Open <https://jcmamoa.github.io/invoice-pipeline-demo/>.
2. Click a sample or drop a text-based PDF.
3. Compare the four samples above; nothing leaves the browser.

### Test the full pipeline

```bash
# upload one invoice
curl -s -X POST http://localhost:8000/api/invoices \
  -F file=@samples/acme_invoice.pdf | jq

# inspect status and audit trail
curl -s http://localhost:8000/api/stats | jq
curl -s http://localhost:8000/api/invoices | jq '.invoices[].status'

# drop a file in the watched folder
docker compose cp ./my-invoice.pdf worker:/data/inbox/

# follow the worker
docker compose logs -f worker
```

In the UI, click any row to see the normalized fields, validation reasons, raw
extraction JSON, notification and the full audit trail. The original PDF is
available through **Download original PDF**.

---

## Notifications

| Channel | Payload | Fallback |
|---|---|---|
| Slack | Block Kit: header, message, supplier/CNPJ/total/due fields | plain `text` |
| Teams | `MessageCard` with the same facts | plain `text` |
| none configured | `mock` — stored in Postgres and shown in the UI feed | — |

Set `SLACK_WEBHOOK_URL` or `TEAMS_WEBHOOK_URL` in `.env`. If a webhook rejects
the rich payload (`400/413/415/422`), the client retries once with `{"text": …}`.
A webhook failure is recorded but never rolls back the invoice.

Message language is `en` by default and can be switched with
`NOTIFY_LANGUAGE=pt-BR`.

---

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/invoices` | upload a PDF; `202` new, `200` duplicate |
| `GET` | `/api/invoices` | list with optional `status`, `limit`, `offset` |
| `GET` | `/api/invoices/{id}` | invoice + notifications + audit trail |
| `GET` | `/api/invoices/{id}/file` | download the archived original |
| `POST` | `/api/invoices/{id}/retry` | requeue `failed` / `needs_review` |
| `GET` | `/api/notifications` | recent notification feed |
| `GET` | `/api/stats` | counts by status + queue depth |
| `GET` | `/healthz`, `/readyz`, `/metrics` | operations |

Full reference: [`docs/API.md`](docs/API.md).

---

## Reliability and observability

- **Idempotency** on the SHA-256 of the file: upload, folder and IMAP copies of
  the same invoice collapse into one row.
- **Durable queue** in Postgres (`FOR UPDATE SKIP LOCKED`), no broker to operate.
- **Retries** with backoff for transient LLM errors; manual re-process from the UI.
- **Crash recovery**: stale `processing` claims are requeued automatically.
- **Audit trail**: `ingested`, `processing_started`, `extracted`, `processed`,
  `needs_review`, `failed`, `retry_scheduled`, `notification_sent`/`failed`.
- **Metrics** (Prometheus):

| Metric | Meaning |
|---|---|
| `invoice_ingested_total{source,duplicate}` | intake volume |
| `invoice_processed_total{status}` | terminal outcomes |
| `invoice_extraction_duration_seconds{mode}` | extraction latency |
| `invoice_ocr_total{result}` | OCR outcomes |
| `invoice_notifications_total{channel,status}` | delivery results |
| `invoice_queue_depth` | queued + processing |
| `invoice_api_requests_total{method,route,status}` | API traffic |

Logs are JSON with `invoice_id`, `status`, `attempt` and `error` fields.

---

## Configuration

Copy `.env.example` to `.env`. The most relevant variables:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_MODE` | `mock` | `mock`, `openai` or `auto` |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `LLM_API_KEY` | empty | required for `openai` |
| `LLM_MODEL` | `gpt-4o-mini` | model name |
| `MIN_CONFIDENCE` | `0.55` | below this the invoice needs review |
| `OCR_ENABLED` | `true` | OCR fallback for scans |
| `OCR_LANG` | `eng` | Tesseract languages (`eng+por`) |
| `OCR_DPI` | `200` | render resolution |
| `OCR_MAX_PAGES` | `5` | CPU bound per document |
| `SLACK_WEBHOOK_URL` / `TEAMS_WEBHOOK_URL` | empty | notification channels |
| `NOTIFY_LANGUAGE` | `en` | `en` or `pt-BR` |
| `DEMO_MODE` | `true` | enables `/api/demo/*` |
| `IMAP_ENABLED` | `false` | optional e-mail ingestion |

Full table and runbooks: [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

---

## Tests and CI

```bash
pip install -e ".[dev]"
ruff check .
pytest                                   # unit tests, no database
TEST_DATABASE_URL='postgresql://invoice:invoice@localhost:5434/invoice' \
  pytest tests/test_api_integration.py -v
```

- 27 tests: parsing/validation, deterministic extractor, real OCR against
  `scanned_invoice.pdf`, notification payloads and an end-to-end API integration
  test against Postgres (the integration test skips when `TEST_DATABASE_URL` is
  not set).
- OCR tests skip automatically when Tesseract is not installed.
- GitHub Actions: `unit tests` (lint + pytest + compose config), `api
  integration` (Postgres service + schema + E2E) and `build image`.

---

## Repository layout

```
db/001_schema.sql              invoices, audit_log, notifications
src/invoice_pipeline/
  api.py                       FastAPI app and routes
  worker.py                    watcher + consumer + reaper + IMAP
  extractor/
    pdf.py                     text layer (pypdf)
    ocr.py                     image-only PDFs (pypdfium2 + Tesseract)
    mock.py                    deterministic extractor
    llm.py                     OpenAI-compatible client + strict prompt
    validate.py                CNPJ, money, date, confidence
  pipeline/
    ingest.py                  validation, SHA-256, archive, enqueue
    repository.py              Postgres queries and queue claim
    processor.py               one invoice, end to end
  notifier.py                  Slack / Teams / mock
web/                           vanilla JS UI
scripts/make_samples.py        sample PDFs (including the scanned one)
docs/                          architecture, API, operations, ADRs
```

---

## Security and limits

This repository is a **local demo**, not a hardened deployment:

- No authentication; `DEMO_MODE=true` exposes destructive demo endpoints.
- Default database credentials; ports bound to the host; no TLS.
- LLM mode sends the extracted text to the configured provider; `mock` and OCR
  are fully local.

Before exposing it: authenticate callers, derive the tenant from the credential,
disable demo endpoints, rotate credentials, move the archive to object storage
with lifecycle policies and add rate limits. See
[`docs/DECISIONS.md`](docs/DECISIONS.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Roadmap

1. Tenant/API-key authentication and per-tenant quotas.
2. Outbox pattern so notifications survive a crash between commit and webhook.
3. ERP export (CSV/webhook) and a reconciliation report.
4. OpenTelemetry tracing across API → queue → worker → webhook.
5. Retention/partitioning for `invoices`, `audit_log` and the archive.
6. Better OCR: deskew, cropping and per-field confidence.

## License

MIT. See [LICENSE](LICENSE). The public demo lives at
<https://github.com/JCmamoa/invoice-pipeline-demo>.
