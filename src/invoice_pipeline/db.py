"""Postgres connection pool helpers."""

from __future__ import annotations

import logging
import time

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().database_url,
            min_size=1,
            max_size=10,
            timeout=10,
            open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def wait_for_db(timeout: float = 60.0) -> None:
    """Block until Postgres accepts a query, or raise."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with get_pool().connection() as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001 - retried below
            last_error = exc
            log.warning("waiting for postgres: %s", exc)
            time.sleep(2)
    raise RuntimeError(f"postgres did not become ready: {last_error}")


def db_ok() -> bool:
    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - readiness probe
        return False


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
