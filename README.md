# llm-curator

A self-contained service that keeps track of LLMs — which models are free, which are paid, which have been deprecated — across providers like OpenRouter and Ollama Cloud. It runs daily evals, compares results against your current LiteLLM routing config, and surfaces proposals for what to change. It never auto-applies anything.

Originally part of PARCON. Now lives here as a standalone tool with its own database and scheduler.

---

## What it does

- **Discovery** — crawls OpenRouter and Ollama Cloud daily, upserts models into the registry, flags deprecated entries after 30 days of absence
- **Evals** — runs one model per day against 6 fixed prompts (reasoning, extraction, classification, summarization), scores them, and stores results
- **Proposals** — every Sunday, compares eval leaderboard against what's live in `litellm_config.yaml` and generates a structured proposal: what to replace, what to add, what to remove. Never writes to any config automatically.
- **Alerts** — detects in-use models that have gone paid, deprecated, or are missing evals; sends Telegram notifications if configured

---

## What's in the stack

| Container | Role |
|---|---|
| `db` | Postgres 16 — the curator's own database (`curator`). Schema applied automatically from `migrations/` on first boot. Exposed on `127.0.0.1:5434`. |
| `curator` | Python package + system cron running the four scheduled jobs. |

---

## Prerequisites

- Docker and Docker Compose (Compose v2+)
- An OpenRouter API key (for discovery and evals)
- Ollama running locally or on a reachable host (for Ollama Cloud discovery)
- Optional: Telegram bot token + chat ID for alerts

---

## Setup

**1. Clone and configure**

```bash
cd /home/sriram/llm-curator   # or wherever the repo lives
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```
POSTGRES_PASSWORD=<strong-password>
OPENROUTER_API_KEY=<your-key>
```

Everything else is optional. Telegram alerts are silent no-ops if the bot token and chat ID are absent.

**2. Start the database**

```bash
docker compose up -d db
```

Postgres starts, creates the `curator` database, and runs the four migration files in `migrations/` automatically. Wait a few seconds for the healthcheck to pass.

**3. (First time only) Migrate existing data from PARCON**

If you are moving from PARCON and want to bring across the existing model registry, evals, proposals, and alerts, run the migration script. It is read-only on PARCON — it never writes to or alters PARCON's database.

```bash
POSTGRES_PASSWORD=<same-as-.env> bash scripts/migrate_data.sh
```

This uses `docker exec pgvector` to pg_dump the five curator tables out of PARCON's `parcon_csr` and loads them into `curator` via the running `db` container. Skip this step for a clean-slate install.

**4. Start the scheduler**

```bash
docker compose up -d
```

Both containers start. The cron jobs run on their IST schedule from here on.

---

## Schedule (IST)

| Job | Time | What it does |
|---|---|---|
| Eval runner | daily 02:37 | Picks the least-recently-evaluated eligible model, runs 6 prompts, stores scores |
| OpenRouter discovery | daily 03:17 | Fetches all OpenRouter models, upserts registry, marks deprecated |
| Ollama Cloud discovery | daily 03:43 | Scrapes Ollama Cloud, tests each model, flags paid-only |
| Proposal generator | Sunday 09:23 | Compares leaderboard vs live LiteLLM config, generates a proposal |

---

## CLI usage

Run any command as a one-shot without touching the scheduler:

```bash
# Registry overview
docker compose run --rm curator python -m llm_curator.cli stats

# Eval leaderboard (all use cases)
docker compose run --rm curator python -m llm_curator.cli leaderboard

# Leaderboard for a specific use case
docker compose run --rm curator python -m llm_curator.cli leaderboard --use-case summarization

# Show the latest proposal
docker compose run --rm curator python -m llm_curator.cli proposals

# Show alerts
docker compose run --rm curator python -m llm_curator.cli alerts

# Acknowledge an alert
docker compose run --rm curator python -m llm_curator.cli ack <id> --note "reviewed"

# Run discovery manually (useful after a long offline period)
docker compose run --rm curator python -m llm_curator.openrouter_discovery
docker compose run --rm curator python -m llm_curator.ollama_cloud_discovery
```

---

## Configuration reference

All configuration is via `.env`. See `.env.example` for the full list.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `POSTGRES_PASSWORD` | Yes | — | Password for the `curator` database |
| `OPENROUTER_API_KEY` | Yes | — | Used for discovery and evals |
| `OLLAMA_URL` | No | `http://host.docker.internal:11434` | Point elsewhere if Ollama is on another machine |
| `PARCON_LITELLM_CONFIG` | No | unset | Absolute host path to PARCON's `litellm_config.yaml`. Mounted read-only into the container. Unset to run with zero PARCON contact. |
| `TELEGRAM_BOT_TOKEN` | No | — | Both token and chat ID must be set for alerts to send |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat to receive alert messages |

---

## Relationship to PARCON

This repo was extracted from `parcon/sahay/llm_curator/`. The Python package is a verbatim copy — no code edits were needed because every coupling point was already env-driven. The only optional link back to PARCON is the `PARCON_LITELLM_CONFIG` mount, which lets the proposal generator compare its recommendations against what PARCON is actually running. Remove that env var and the curator is entirely self-contained.

PARCON still holds the original code in `sahay/llm_curator/` until Stage 3 of the extraction is complete (removal of the code, systemd timers, and dashboard widgets from PARCON).
