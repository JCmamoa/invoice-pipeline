SHELL := /bin/bash
COMPOSE ?= docker compose
PYTHON ?= python3
BASE ?= http://localhost:8000

.PHONY: help up wait down reset build ps logs seed sample samples test lint fmt archive clean

help:
	@echo "invoice-pipeline — phase 0"
	@echo ""
	@echo "  make up        build and start the stack"
	@echo "  make wait      wait until /readyz returns 200"
	@echo "  make seed      enqueue the bundled sample invoices"
	@echo "  make sample    download one sample PDF to ./sample-invoice.pdf"
	@echo "  make samples   regenerate the sample PDFs"
	@echo "  make ps        container status"
	@echo "  make logs      follow api + worker logs"
	@echo "  make down      stop the stack, keep volumes"
	@echo "  make reset     stop and delete all volumes (fresh start)"
	@echo "  make test      run unit tests (needs pip install -e '.[dev]')"
	@echo "  make lint      run ruff"
	@echo "  make fmt       format with ruff"
	@echo "  make build     build the image"
	@echo "  make clean     remove local python caches"

up:
	$(COMPOSE) up -d --build

wait:
	@until curl -sf $(BASE)/readyz >/dev/null; do echo "waiting for $(BASE)/readyz"; sleep 2; done
	@echo "ready: $(BASE)"

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v

build:
	$(COMPOSE) build

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f api worker

seed:
	@curl -sf -X POST $(BASE)/api/demo/seed | $(PYTHON) -m json.tool
	@echo "queued. open $(BASE) to watch the pipeline."

sample:
	@curl -sf -o sample-invoice.pdf $(BASE)/api/demo/samples/acme_invoice.pdf
	@echo "wrote sample-invoice.pdf. Upload it at $(BASE)."

samples:
	$(PYTHON) scripts/make_samples.py

test:
	$(PYTHON) -m pytest

lint:
	ruff check .

fmt:
	ruff format .

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ *.egg-info build dist
