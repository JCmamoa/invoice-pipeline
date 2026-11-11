"""Worker process: folder watcher + queue consumer + reaper."""

from __future__ import annotations

import logging
import signal
import threading
import time

from prometheus_client import start_http_server

from .config import get_settings
from .db import wait_for_db
from .logging_setup import setup_logging
from .pipeline.imap import ImapIngestor
from .pipeline.loops import DirectoryWatcher, QueueWorker, StaleClaimReaper

log = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    settings = get_settings()
    settings.ensure_dirs()
    wait_for_db(timeout=60)

    try:
        start_http_server(settings.metrics_port, addr="0.0.0.0")
        log.info("worker metrics listening on :%d", settings.metrics_port)
    except OSError as exc:
        log.warning("could not start metrics server: %s", exc)

    stop_event = threading.Event()
    threads: list[threading.Thread] = [
        DirectoryWatcher(settings, stop_event),
        QueueWorker(settings, stop_event),
        StaleClaimReaper(settings, stop_event),
    ]
    if settings.imap_enabled:
        threads.append(ImapIngestor(settings, stop_event))

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    for thread in threads:
        thread.start()

    while not stop_event.is_set():
        time.sleep(1)

    for thread in threads:
        thread.join(timeout=5)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
