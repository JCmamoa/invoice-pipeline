"""Background threads: folder watcher, queue consumer and stale-claim reaper."""

from __future__ import annotations

import logging
import threading

from .. import metrics
from ..config import Settings
from . import repository
from .ingest import ingest_directory
from .processor import process_next

log = logging.getLogger(__name__)


class DirectoryWatcher(threading.Thread):
    """Polls the inbox folder and enqueues settled PDFs."""

    def __init__(self, settings: Settings, stop_event: threading.Event) -> None:
        super().__init__(name="directory-watcher", daemon=True)
        self.settings = settings
        self.stop_event = stop_event

    def run(self) -> None:
        log.info("folder watcher started: %s", self.settings.inbox_dir)
        while not self.stop_event.is_set():
            try:
                for row, duplicate in ingest_directory(self.settings):
                    metrics.INVOICES_INGESTED.labels(
                        source=row["source"], duplicate=str(duplicate).lower()
                    ).inc()
            except Exception:  # noqa: BLE001 - keep the watcher alive
                log.exception("folder watcher iteration failed")
            self.stop_event.wait(self.settings.watcher_poll_seconds)


class QueueWorker(threading.Thread):
    """Claims and processes invoices until the queue is empty."""

    def __init__(self, settings: Settings, stop_event: threading.Event) -> None:
        super().__init__(name="queue-worker", daemon=True)
        self.settings = settings
        self.stop_event = stop_event

    def run(self) -> None:
        log.info("queue worker started")
        while not self.stop_event.is_set():
            try:
                worked = process_next(self.settings)
                metrics.QUEUE_DEPTH.set(repository.queue_depth())
                if not worked:
                    self.stop_event.wait(self.settings.worker_poll_seconds)
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("queue worker iteration failed")
                self.stop_event.wait(self.settings.worker_poll_seconds)


class StaleClaimReaper(threading.Thread):
    """Recovers invoices whose worker died mid-processing."""

    def __init__(self, settings: Settings, stop_event: threading.Event) -> None:
        super().__init__(name="stale-claim-reaper", daemon=True)
        self.settings = settings
        self.stop_event = stop_event

    def run(self) -> None:
        log.info("stale claim reaper started")
        while not self.stop_event.is_set():
            try:
                recovered = repository.reap_stale(
                    self.settings.stale_processing_seconds, self.settings.max_attempts
                )
                if recovered:
                    log.warning("recovered %d stale processing claim(s)", recovered)
            except Exception:  # noqa: BLE001 - keep the reaper alive
                log.exception("reaper iteration failed")
            self.stop_event.wait(30)
