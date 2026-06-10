"""DB helper for llm-curator.

Lazy connection with reconnect-on-drop.
Configure via DATABASE_URL env var (or CSR_DSN for legacy compat).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DSN = os.getenv(
    "DATABASE_URL",
    os.getenv("CSR_DSN", "postgresql://curator_user:changeme@localhost:5434/curator"),
)

_conn: Optional[psycopg2.extensions.connection] = None


def get_conn() -> psycopg2.extensions.connection:
    """Return a live psycopg2 connection, reconnecting if dropped."""
    global _conn
    try:
        if _conn and not _conn.closed:
            _conn.cursor().execute("SELECT 1")
            return _conn
    except Exception:
        pass
    _conn = psycopg2.connect(DSN)
    _conn.autocommit = False
    logger.info("llm_curator: connected to PostgreSQL")
    return _conn


@contextmanager
def cursor():
    """Context manager yielding a RealDictCursor; commits on success, rolls back on error."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
