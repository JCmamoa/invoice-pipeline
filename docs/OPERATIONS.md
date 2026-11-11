# Operations

## Prerequisites

- Docker 24+ with Compose v2.
- ~1 GB of free RAM and ~2 GB of disk.
- Free ports: `8000` (API/UI), `8001` (worker metrics), `5434` (Postgres on the
  host; the container uses 5432).

No Python or Go is required on the host to run the stack.

## Start, stop and reset

```bash
docker compose up -d --build
curl -sf http://localhost:8000/readyz

docker compose down            # keeps volumes
docker compose down -v         # deletes Postgres and archive data
```

The first start applies `db/001_schema.sql` through
`/docker-entrypoint-initdb.d`. If you change the schema, run `down -v` or apply
the change manually.

## Environment

Copy `.env.example` to `.env` and edit it, or export variables before
`docker compose up`.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | compose-internal DSN | Postgres |
| `LLM_MODE` | `mock` | `mock`, `openai` or `auto` |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `LLM_API_KEY` | empty | required for `openai` |
| `LLM_MODEL` | `gpt-4o-mini` | model name |
| `LLM_TIMEOUT` | `60` | request timeout (seconds) |
| `MIN_CONFIDENCE` | `0.55` | below this the invoice needs review |
| `SLACK_WEBHOOK_URL` | empty | Slack incoming webhook |
| `TEAMS_WEBHOOK_URL` | empty | Teams webhook |
| `NOTIFY_LANGUAGE` | `en` | `en` or `pt-BR` |
| `MAX_ATTEMPTS` | `3` | retries for transient extraction errors |
| `STALE_PROCESSING_SECONDS` | `300` | reaper threshold |
| `MAX_PDF_BYTES` | `15728640` | upload limit |
| `DEMO_MODE` | `true` | enables `/api/demo/*` |
| `OCR_ENABLED` | `true` | OCR fallback for PDFs without a text layer |
| `OCR_LANG` | `eng` | Tesseract language(s), e.g. `eng+por` |
| `OCR_DPI` | `200` | render resolution for OCR |
| `OCR_MAX_PAGES` | `5` | pages OCRed per document |
| `OCR_MIN_CHARS` | `25` | below this OCR is considered empty |
| `IMAP_ENABLED` | `false` | optional email ingestion |
| `IMAP_HOST/PORT/USER/PASSWORD/FOLDER/SSL` | — | mailbox settings |
| `IMAP_POLL_SECONDS` | `60` | mailbox polling interval |

Notification language: the artifacts (code, docs, logs, commits) are English.
The manager-facing message can be localized with `NOTIFY_LANGUAGE=pt-BR`; see
`src/invoice_pipeline/notifier.py`.

## Using a real LLM

```bash
cat > .env <<'EOF'
LLM_MODE=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
EOF
docker compose up -d
```

For a local model with Ollama:

```bash
LLM_MODE=openai LLM_BASE_URL=http://host.docker.internal:11434/v1 \
  LLM_API_KEY=ollama LLM_MODEL=llama3.1 docker compose up -d
```

The extractor retries `429` and `5xx` responses. A `400` is usually a provider
that rejects `response_format`; the client retries once without it.

## Triggers

### Upload

Use the UI at <http://localhost:8000> or:

```bash
curl -X POST http://localhost:8000/api/invoices -F file=@invoice.pdf
```

### Folder

```bash
docker compose cp ./invoice.pdf worker:/data/inbox/
docker compose logs -f worker
```

The watcher waits `INBOX_SETTLE_SECONDS` before reading a file, then moves it to
`/data/inbox/processed/`.

### IMAP

```bash
IMAP_ENABLED=true IMAP_HOST=imap.example.com IMAP_USER=... IMAP_PASSWORD=... \
  docker compose up -d worker
```

Only `application/pdf` attachments are ingested. Messages are marked `\Seen`
after every attachment is enqueued. The poller reconnects on errors.

## Notifications

- Slack: create an Incoming Webhook and set `SLACK_WEBHOOK_URL`. The payload is
  Block Kit (header, message and supplier/CNPJ/total/due fields) with a plain
  `text` fallback.
- Teams: set `TEAMS_WEBHOOK_URL`; the payload is a `MessageCard`.
- If a tenant still uses an older incoming webhook, the client retries once with
  `{"text": ...}` when the first payload is rejected with `400/413/415/422`.
- No webhook: status `mock`, visible in the UI and in the `notifications` table.

A webhook failure is recorded (`status=failed`) but never rolls back the
invoice, because the data is already committed.

## OCR fallback

The image ships `tesseract-ocr` with `eng` and `por` models. When a PDF has no
text layer:

1. Up to `OCR_MAX_PAGES` pages are rendered at `OCR_DPI`.
2. Tesseract reads them with `OCR_LANG`.
3. If the result has at least `OCR_MIN_CHARS` characters, it goes through the
   same extractor and validator as a digital invoice.
4. Otherwise the invoice is `needs_review` with `no_text_layer`.

The invoice records where the text came from in `text_source`: `text_layer`,
`ocr` or `none`. Watch `invoice_ocr_total{result}` for `ocr_unavailable`,
`ocr_failed` and `ocr_empty`.

To add a language, install the matching Tesseract package in the image (for
example `tesseract-ocr-spa`) and set `OCR_LANG=eng+spa`. OCR is CPU-bound:
expect roughly 1-3 seconds per page.

## Observability

| URL | What |
|---|---|
| `http://localhost:8000/metrics` | API metrics |
| `http://localhost:8001/metrics` | worker metrics |
| `http://localhost:8000/docs` | OpenAPI UI |
| `docker compose logs -f api worker` | JSON logs |

Useful queries:

```bash
# queue and outcomes
curl -s localhost:8000/api/stats | jq

# recent notifications
curl -s 'localhost:8000/api/notifications?limit=10' | jq

# audit trail for one invoice
curl -s localhost:8000/api/invoices/<id> | jq '.audit'

# database view
docker compose exec postgres psql -U invoice -d invoice -c \
  "select status, count(*) from invoices group by 1 order by 2 desc"
```

## Runbooks

### API returns 503 on `/readyz`

```bash
docker compose ps
docker compose logs --tail=50 postgres api
```

The API waits up to 60s for Postgres on startup. If the database is in crash
recovery, wait for `pg_isready`; do not delete the volume.

### Invoice stuck in `processing`

The reaper requeues claims older than `STALE_PROCESSING_SECONDS`. Check the
worker:

```bash
docker compose logs --tail=80 worker
docker compose restart worker
```

### Invoice in `needs_review`

Open the invoice in the UI; `validation_errors` explains why. Common causes:

| Error | Action |
|---|---|
| `missing_due_date` | the PDF does not state a due date; fix the source or add it manually |
| `invalid_cnpj` | checksum failed; verify the supplier document |
| `missing_total` | the total is not in the text layer |
| `no_text_layer` | scanned PDF; OCR is disabled, unavailable or found too little text |
| `low_confidence` | lower `MIN_CONFIDENCE` or inspect the extraction JSON |

Use **Re-process** after fixing the source or the configuration.

### OCR is not finding text

```bash
docker compose exec worker tesseract --list-langs
docker compose exec worker sh -c 'echo $OCR_LANG $OCR_ENABLED'
curl -s localhost:8001/metrics | grep invoice_ocr
```

| Metric result | Meaning |
|---|---|
| `ocr_unavailable` | Tesseract binary missing in the image |
| `ocr_failed` | Tesseract raised an error while reading a page |
| `ocr_empty` | the scan is blank, too small or below `OCR_MIN_CHARS` |

If the text is found but the fields are not, increase `OCR_DPI` (300 helps on
small fonts) and check the extracted JSON in the UI. Re-process the invoice
after changing OCR settings.

### LLM failures

```bash
docker compose logs --tail=50 worker | grep -i llm
```

- `401`: wrong `LLM_API_KEY`.
- `404`: wrong `LLM_BASE_URL` or `LLM_MODEL`.
- `429`/`5xx`: transient; the invoice is retried with backoff.
- `model did not return JSON`: the prompt was ignored; the invoice is retried.

### Disk full

The largest consumers are the `pgdata` and `data` volumes. `archive/` grows with
every unique PDF. There is no automatic retention; plan a lifecycle policy or
object storage for production.

### Reset everything

```bash
docker compose down -v
docker compose up -d --build
```

Or, while running, `POST /api/demo/reset` (when `DEMO_MODE=true`).

## Backup

```bash
docker compose exec -T postgres pg_dump -U invoice invoice > invoices.sql
docker run --rm -v invoice-pipeline_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/archive.tgz -C /data archive
```
