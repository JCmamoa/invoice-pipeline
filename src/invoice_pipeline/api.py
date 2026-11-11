"""FastAPI application: upload, invoice querying, demo controls and the web UI."""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics
from .config import get_settings
from .db import close_pool, db_ok, wait_for_db
from .logging_setup import setup_logging
from .pipeline import repository
from .pipeline.ingest import InvalidUpload, archived_file, enqueue_invoice

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    settings = get_settings()
    settings.ensure_dirs()
    wait_for_db(timeout=60)
    log.info("api ready")
    yield
    close_pool()


app = FastAPI(
    title="Invoice Pipeline",
    version="0.1.0",
    description=(
        "Automated invoice ingestion: PDF trigger, strict extraction, validation, "
        "audit trail and notifications."
    ),
    lifespan=lifespan,
)

_settings = get_settings()
if _settings.web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_settings.web_dir)), name="static")


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    metrics.API_REQUESTS.labels(
        method=request.method, route=path, status=str(response.status_code)
    ).inc()
    return response


def serialise(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    if out.get("invoice_id") is not None:
        out["invoice_id"] = str(out["invoice_id"])
    for key in ("created_at", "updated_at", "processed_at", "next_attempt_at"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    if out.get("due_date") is not None and hasattr(out["due_date"], "isoformat"):
        out["due_date"] = out["due_date"].isoformat()
    if out.get("total_amount") is not None:
        out["total_amount"] = str(out["total_amount"])
    return out


@app.get("/", include_in_schema=False)
def index():
    index_file = get_settings().web_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="web UI not installed")
    return FileResponse(index_file)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    if db_ok():
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not_ready"}, status_code=503)


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/invoices", status_code=202)
def upload_invoice(
    file: Annotated[UploadFile, File()],
    source: Annotated[str, Query()] = "upload",
):
    settings = get_settings()
    data = file.file.read(settings.max_pdf_bytes + 1)
    try:
        row, duplicate = enqueue_invoice(
            settings,
            filename=file.filename or "invoice.pdf",
            data=data,
            source=source,
            source_ref=file.filename,
        )
    except InvalidUpload as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

    metrics.INVOICES_INGESTED.labels(
        source=source, duplicate=str(duplicate).lower()
    ).inc()
    payload = {"invoice": serialise(row), "duplicate": duplicate}
    return JSONResponse(status_code=200 if duplicate else 202, content=jsonable_encoder(payload))


@app.get("/api/invoices")
def list_invoices(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if status and status not in repository.STATUSES:
        raise HTTPException(status_code=400, detail="unknown status")
    rows = repository.list_invoices(status=status, limit=limit, offset=offset)
    return {"invoices": [serialise(row) for row in rows], "count": len(rows)}


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str) -> dict[str, Any]:
    row = repository.get_invoice(invoice_id)
    if row is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    return {
        "invoice": serialise(row),
        "notifications": [
            serialise(item) for item in repository.notifications_for(invoice_id)
        ],
        "audit": [serialise(item) for item in repository.audit_for(invoice_id)],
    }


@app.get("/api/invoices/{invoice_id}/file")
def get_invoice_file(invoice_id: str):
    row = repository.get_invoice(invoice_id)
    if row is None or not row.get("archived_path"):
        raise HTTPException(status_code=404, detail="invoice not found")
    try:
        path = archived_file(get_settings(), row["archived_path"])
    except InvalidUpload as exc:
        raise HTTPException(status_code=404, detail="archive unavailable") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="archived file missing")
    return FileResponse(
        path, media_type="application/pdf", filename=row["original_filename"]
    )


@app.post("/api/invoices/{invoice_id}/retry", status_code=202)
def retry_invoice(invoice_id: str) -> dict[str, Any]:
    if not repository.requeue_manual(invoice_id):
        raise HTTPException(status_code=409, detail="invoice is not retryable")
    repository.record_audit(invoice_id, "retry_requested", {})
    row = repository.get_invoice(invoice_id)
    return {"invoice": serialise(row) if row else None}


@app.get("/api/notifications")
def list_notifications(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    rows = repository.recent_notifications(limit=limit)
    return {"notifications": [serialise(row) for row in rows]}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    counts = repository.status_counts()
    counts["queue_depth"] = repository.queue_depth()
    return counts


@app.get("/api/demo/samples")
def list_samples() -> dict[str, Any]:
    settings = get_settings()
    if not settings.sample_dir.exists():
        return {"samples": []}
    return {
        "samples": sorted(path.name for path in settings.sample_dir.glob("*.pdf"))
    }


@app.get("/api/demo/samples/{name}")
def download_sample(name: str):
    settings = get_settings()
    candidate = (settings.sample_dir / name).resolve()
    root = settings.sample_dir.resolve()
    if root not in candidate.parents or not candidate.exists():
        raise HTTPException(status_code=404, detail="sample not found")
    return FileResponse(candidate, media_type="application/pdf", filename=name)


@app.post("/api/demo/seed", status_code=202)
def seed_demo() -> dict[str, Any]:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="demo mode is disabled")
    if not settings.sample_dir.exists():
        raise HTTPException(status_code=404, detail="no samples available")

    queued = []
    for path in sorted(settings.sample_dir.glob("*.pdf")):
        row, duplicate = enqueue_invoice(
            settings,
            filename=path.name,
            data=path.read_bytes(),
            source="demo",
            source_ref=path.name,
        )
        metrics.INVOICES_INGESTED.labels(
            source="demo", duplicate=str(duplicate).lower()
        ).inc()
        queued.append({"invoice": serialise(row), "duplicate": duplicate})
    return {"queued": queued}


@app.post("/api/demo/reset")
def reset_demo() -> dict[str, Any]:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="demo mode is disabled")

    repository.reset_demo_data()
    archive = settings.archive_dir
    if archive.exists():
        for child in archive.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    return {"status": "reset"}


@app.get("/api/health/dependencies", include_in_schema=False)
def dependencies() -> dict[str, bool]:
    return {"postgres": db_ok()}
