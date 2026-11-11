-- Invoice automation schema.
--
-- The invoices table doubles as the work queue. A row is inserted as 'queued';
-- a worker claims it with SELECT ... FOR UPDATE SKIP LOCKED and flips it to
-- 'processing'. Terminal states are 'processed' (all required fields validated),
-- 'needs_review' (extraction incomplete or invalid) and 'failed' (processing
-- error after all retries).
--
-- sha256 is UNIQUE: the same file uploaded twice is never processed twice.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS invoices (
    id                  UUID PRIMARY KEY,
    source              TEXT        NOT NULL,
    source_ref          TEXT,
    original_filename   TEXT        NOT NULL,
    sha256              TEXT        NOT NULL UNIQUE,
    status              TEXT        NOT NULL DEFAULT 'queued',
    supplier_name       TEXT,
    supplier_cnpj       TEXT,
    total_amount        NUMERIC(18, 2),
    currency            TEXT,
    due_date            DATE,
    confidence          NUMERIC(4, 3),
    extraction          JSONB,
    validation_errors   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    validation_warnings JSONB       NOT NULL DEFAULT '[]'::jsonb,
    llm_mode            TEXT,
    text_source         TEXT,
    archived_path       TEXT,
    attempts            INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ,
    CONSTRAINT invoices_status_check CHECK (
        status IN ('queued', 'processing', 'processed', 'needs_review', 'failed')
    )
);

-- Existing installations: keep the schema forward-compatible.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS text_source TEXT;

CREATE INDEX IF NOT EXISTS invoices_status_idx
    ON invoices (status, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS invoices_created_idx
    ON invoices (created_at DESC);
CREATE INDEX IF NOT EXISTS invoices_supplier_idx
    ON invoices (supplier_cnpj);

CREATE TABLE IF NOT EXISTS notifications (
    id          BIGSERIAL PRIMARY KEY,
    invoice_id  UUID        NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    channel     TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    message     TEXT,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS notifications_invoice_idx
    ON notifications (invoice_id);
CREATE INDEX IF NOT EXISTS notifications_created_idx
    ON notifications (created_at DESC);

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    invoice_id  UUID,
    action      TEXT        NOT NULL,
    detail      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_invoice_idx
    ON audit_log (invoice_id, created_at);
