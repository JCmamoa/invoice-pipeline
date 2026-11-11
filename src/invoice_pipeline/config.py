"""Environment-driven configuration.

Every value has a safe local default so the demo runs with no `.env` file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _project_root() -> Path:
    # src/invoice_pipeline/config.py -> repository root
    return Path(__file__).resolve().parents[2]


def _default_dir(env_name: str, container_path: str, dev_path: Path) -> Path:
    raw = _env(env_name)
    if raw:
        return Path(raw).expanduser()
    container = Path(container_path)
    if container.exists():
        return container
    return dev_path


@dataclass(frozen=True)
class Settings:
    database_url: str
    inbox_dir: Path
    archive_dir: Path
    sample_dir: Path
    web_dir: Path
    metrics_port: int
    llm_mode: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    min_confidence: float
    slack_webhook_url: str
    teams_webhook_url: str
    notify_language: str
    worker_poll_seconds: float
    watcher_poll_seconds: float
    inbox_settle_seconds: float
    max_attempts: int
    stale_processing_seconds: int
    max_pdf_bytes: int
    demo_mode: bool
    public_base_url: str
    imap_enabled: bool
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str
    imap_ssl: bool
    imap_poll_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        root = _project_root()
        return cls(
            database_url=_env(
                "DATABASE_URL", "postgresql://invoice:invoice@localhost:5434/invoice"
            ),
            inbox_dir=_default_dir("INBOX_DIR", "/data/inbox", root / "data" / "inbox"),
            archive_dir=_default_dir(
                "ARCHIVE_DIR", "/data/archive", root / "data" / "archive"
            ),
            sample_dir=_default_dir("SAMPLE_DIR", "/app/samples", root / "samples"),
            web_dir=_default_dir("WEB_DIR", "/app/web", root / "web"),
            metrics_port=_env_int("METRICS_PORT", 8001),
            llm_mode=_env("LLM_MODE", "mock").lower(),
            llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=_env("LLM_API_KEY"),
            llm_model=_env("LLM_MODEL", "gpt-4o-mini"),
            llm_timeout=_env_float("LLM_TIMEOUT", 60.0),
            min_confidence=_env_float("MIN_CONFIDENCE", 0.55),
            slack_webhook_url=_env("SLACK_WEBHOOK_URL"),
            teams_webhook_url=_env("TEAMS_WEBHOOK_URL"),
            notify_language=_env("NOTIFY_LANGUAGE", "en"),
            worker_poll_seconds=_env_float("WORKER_POLL_SECONDS", 2.0),
            watcher_poll_seconds=_env_float("WATCHER_POLL_SECONDS", 3.0),
            inbox_settle_seconds=_env_float("INBOX_SETTLE_SECONDS", 2.0),
            max_attempts=_env_int("MAX_ATTEMPTS", 3),
            stale_processing_seconds=_env_int("STALE_PROCESSING_SECONDS", 300),
            max_pdf_bytes=_env_int("MAX_PDF_BYTES", 15 * 1024 * 1024),
            demo_mode=_env_bool("DEMO_MODE", True),
            public_base_url=_env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
            imap_enabled=_env_bool("IMAP_ENABLED", False),
            imap_host=_env("IMAP_HOST"),
            imap_port=_env_int("IMAP_PORT", 993),
            imap_user=_env("IMAP_USER"),
            imap_password=_env("IMAP_PASSWORD"),
            imap_folder=_env("IMAP_FOLDER", "INBOX"),
            imap_ssl=_env_bool("IMAP_SSL", True),
            imap_poll_seconds=_env_float("IMAP_POLL_SECONDS", 60.0),
        )

    def ensure_dirs(self) -> None:
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
