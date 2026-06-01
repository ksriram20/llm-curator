"""DB helper for LLM curator.

Follows the pattern in sahay/brain/kb.py — lazy connection with reconnect-on-drop.
Uses the same CSR_DSN env var so we share PARCON's existing PostgreSQL instance.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DSN = os.getenv(
    "CSR_DSN",
    "postgresql://parcon:parcon2026@localhost:5433/parcon_csr",  # pragma: allowlist secret
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


def cursor():
    """Return a RealDictCursor (rows behave like dicts)."""
    return get_conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor)
