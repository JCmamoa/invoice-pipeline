FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OCR fallback for scanned PDFs: Tesseract plus English and Portuguese models.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

COPY web ./web
COPY db ./db
COPY scripts ./scripts
COPY samples ./samples

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/inbox /data/archive \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000 8001

CMD ["uvicorn", "invoice_pipeline.api:app", "--host", "0.0.0.0", "--port", "8000"]
