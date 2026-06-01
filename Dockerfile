FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Package + notify shim (shim sits at /app so PYTHONPATH picks it up)
COPY llm_curator/ ./llm_curator/
COPY memory_notify.py ./memory_notify.py
COPY crontab.system /etc/cron.d/llm-curator
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0644 /etc/cron.d/llm-curator \
    && chmod +x /entrypoint.sh

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/entrypoint.sh"]
# Default: run the scheduler. Override for one-shot CLI, e.g.:
#   docker compose run --rm curator python -m llm_curator.cli stats
CMD ["cron", "-f"]
