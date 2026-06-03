"""llm-curator FastAPI UI — read-only dashboard (v0.2)."""
from datetime import datetime, date
from decimal import Decimal
from typing import Any


def serialize(row: dict) -> dict[str, Any]:
    """Convert psycopg2 RealDictRow to JSON-safe dict."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out
