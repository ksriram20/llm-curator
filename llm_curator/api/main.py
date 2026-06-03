"""llm-curator FastAPI application — v0.2 read-only UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from llm_curator.api.routers import alerts, leaderboard, proposals, registry, stats

app = FastAPI(
    title="llm-curator",
    version="0.2.0",
    description="Self-hosted LLM evaluation and routing intelligence platform.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── API routers ────────────────────────────────────────────────────────────
app.include_router(stats.router,       prefix="/api/stats",       tags=["stats"])
app.include_router(registry.router,    prefix="/api/registry",    tags=["registry"])
app.include_router(leaderboard.router, prefix="/api/leaderboard", tags=["leaderboard"])
app.include_router(proposals.router,   prefix="/api/proposals",   tags=["proposals"])
app.include_router(alerts.router,      prefix="/api/alerts",      tags=["alerts"])

# ── Static files (HTML pages) — must be mounted last ─────────────────────
STATIC_DIR = Path(__file__).parent.parent.parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
