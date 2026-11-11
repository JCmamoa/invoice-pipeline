"""Ingestion: validate a PDF, archive the original and enqueue it.

Idempotency is keyed on the SHA-256 of the file. Uploading or dropping the same
file twice returns the existing invoice instead of creating a new one.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..config import Settings
from . import repository

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


class InvalidUpload(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_pdf(settings: Settings, invoice_id: str, data: bytes) -> str:
    """Store the original PDF under archive/YYYYMMDD/<invoice-id>.pdf."""
    day = datetime.now(UTC).strftime("%Y%m%d")
    directory = settings.archive_dir / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{invoice_id}.pdf"
    path.write_bytes(data)
    return f"{day}/{invoice_id}.pdf"


def archived_file(settings: Settings, archived_path: str) -> Path:
    # archived_path is relative to ARCHIVE_DIR and stored by us; reject escapes.
    candidate = (settings.archive_dir / archived_path).resolve()
    root = settings.archive_dir.resolve()
    if root not in candidate.parents and candidate != root:
        raise InvalidUpload("invalid archive path")
    return candidate


def enqueue_invoice(
    settings: Settings,
    *,
    filename: str,
    data: bytes,
    source: str,
    source_ref: str | None = None,
) -> tuple[dict, bool]:
    """Archive and enqueue a PDF. Returns (invoice row, duplicate)."""
    if len(data) == 0:
        raise InvalidUpload("empty file")
    if len(data) > settings.max_pdf_bytes:
        raise InvalidUpload("file too large")
    if not data.startswith(PDF_MAGIC):
        raise InvalidUpload("not a PDF")

    sha = sha256_hex(data)
    existing = repository.find_by_sha(sha)
    if existing is not None:
        return existing, True

    invoice_id = str(uuid.uuid4())
    archived_path = archive_pdf(settings, invoice_id, data)
    row, duplicate = repository.insert_invoice(
        invoice_id=invoice_id,
        source=source,
        source_ref=source_ref,
        original_filename=filename,
        sha256=sha,
        archived_path=archived_path,
    )

    if duplicate:
        # A concurrent request inserted the same file first: drop our copy.
        try:
            (settings.archive_dir / archived_path).unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove duplicate archive file %s", archived_path)
        return row, True

    repository.record_audit(
        invoice_id,
        "ingested",
        {"source": source, "source_ref": source_ref, "filename": filename, "sha256": sha},
    )
    log.info(
        "invoice ingested",
        extra={"invoice_id": invoice_id, "source": source, "status": "queued"},
    )
    return row, False


def ingest_directory(
    settings: Settings, settle_seconds: float | None = None
) -> list[tuple[dict, bool]]:
    """Enqueue every settled PDF in the inbox and move it to inbox/processed."""
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = settings.inbox_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    settle = settings.inbox_settle_seconds if settle_seconds is None else settle_seconds

    results: list[tuple[dict, bool]] = []
    now = time.time()
    for path in sorted(settings.inbox_dir.glob("*.pdf")):
        try:
            if now - path.stat().st_mtime < settle:
                continue
            data = path.read_bytes()
            row, duplicate = enqueue_invoice(
                settings,
                filename=path.name,
                data=data,
                source="folder",
                source_ref=path.name,
            )
            destination = processed_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{path.name}"
            path.replace(destination)
            results.append((row, duplicate))
        except InvalidUpload as exc:
            log.warning("skipping %s: %s", path.name, exc.reason)
            path.replace(processed_dir / f"rejected-{path.name}")
        except OSError:
            log.exception("could not ingest %s", path.name)
    return results
