"""Optional IMAP ingestion: download PDF attachments from new messages."""

from __future__ import annotations

import contextlib
import email
import imaplib
import logging
import threading
from email.message import Message

from .. import metrics
from ..config import Settings
from .ingest import InvalidUpload, enqueue_invoice

log = logging.getLogger(__name__)


def _pdf_attachments(message: Message) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename or not filename.lower().endswith(".pdf"):
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append((filename, payload))
    return attachments


class ImapIngestor(threading.Thread):
    """Polls a mailbox for unseen messages with PDF attachments.

    Disabled unless IMAP_ENABLED=true. Messages are marked as seen only after
    every attachment is archived and enqueued.
    """

    def __init__(self, settings: Settings, stop_event: threading.Event) -> None:
        super().__init__(name="imap-ingestor", daemon=True)
        self.settings = settings
        self.stop_event = stop_event

    def _connect(self) -> imaplib.IMAP4:
        if self.settings.imap_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.settings.imap_host, self.settings.imap_port
            )
        else:
            client = imaplib.IMAP4(self.settings.imap_host, self.settings.imap_port)
        client.login(self.settings.imap_user, self.settings.imap_password)
        return client

    def _poll(self) -> None:
        client = self._connect()
        try:
            client.select(self.settings.imap_folder)
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                return
            for uid in data[0].split():
                status, fetched = client.fetch(uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = next(
                    (item[1] for item in fetched if isinstance(item, tuple) and len(item) == 2),
                    None,
                )
                if raw is None:
                    continue
                message = email.message_from_bytes(raw)
                for filename, payload in _pdf_attachments(message):
                    try:
                        row, duplicate = enqueue_invoice(
                            self.settings,
                            filename=filename,
                            data=payload,
                            source="imap",
                            source_ref=f"{uid.decode()}:{filename}",
                        )
                        metrics.INVOICES_INGESTED.labels(
                            source="imap", duplicate=str(duplicate).lower()
                        ).inc()
                        log.info(
                            "invoice ingested from imap",
                            extra={"invoice_id": str(row["id"]), "source": "imap"},
                        )
                    except InvalidUpload as exc:
                        log.warning("imap attachment %s rejected: %s", filename, exc.reason)
                client.store(uid, "+FLAGS", "\\Seen")
        finally:
            with contextlib.suppress(Exception):
                client.logout()

    def run(self) -> None:
        log.info("imap ingestor started: %s@%s", self.settings.imap_user, self.settings.imap_host)
        while not self.stop_event.is_set():
            try:
                self._poll()
            except Exception:  # noqa: BLE001 - reconnect on the next tick
                log.exception("imap poll failed")
            self.stop_event.wait(self.settings.imap_poll_seconds)
