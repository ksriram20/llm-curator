FROM python:3.12-slim

# --- supercronic (cron for containers; logs to stdout, runs as non-root) ---
# Verified against the SHA1SUMS file published with each release (not a hand-typed
# hash). To pin a different version, bump SUPERCRONIC_VERSION.
ARG SUPERCRONIC_VERSION=v0.2.33
ENV SUPERCRONIC_BASE=https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION} \
    SUPERCRONIC=supercronic-linux-amd64

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLO "${SUPERCRONIC_BASE}/${SUPERCRONIC}" \
    && curl -fsSL  "${SUPERCRONIC_BASE}/SHA1SUMS" -o SHA1SUMS \
    && grep " ${SUPERCRONIC}\$" SHA1SUMS | sha1sum -c - \
    && chmod +x "$SUPERCRONIC" \
    && mv "$SUPERCRONIC" /usr/local/bin/supercronic \
    && rm -f SHA1SUMS

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Package + notify shim (shim sits at /app so PYTHONPATH picks it up)
COPY llm_curator/ ./llm_curator/
COPY memory_notify.py ./memory_notify.py
COPY crontab ./crontab

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# Default: run the scheduler. Override for one-shot CLI, e.g.:
#   docker compose run --rm curator python -m llm_curator.cli stats
CMD ["supercronic", "/app/crontab"]
