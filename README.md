# llm-curator

A self-contained service that tracks LLMs — which models are free, which are paid, which have been deprecated — across OpenRouter and Ollama Cloud. It runs scheduled evals, scores models with deterministic graders, and surfaces proposals for what to change in your routing config. It never auto-applies anything.

Fully self-contained: own database, own scheduler, own dashboard. No external dependencies beyond Docker.

---

## What it does

- **Discovery** — crawls OpenRouter and Ollama Cloud daily, upserts models into the registry, flags deprecated entries after 30 days of absence
- **Evals** — runs models against fixed prompts across 6 use cases (reasoning, extraction, classification, summarization, tool use, structured data, code execution), scores with deterministic graders, stores results
- **Proposals** — every Sunday, compares the eval leaderboard against your live routing config and generates a structured proposal: what to replace, what to add, what to remove. Never writes to any config automatically.
- **Alerts** — detects in-use models that have gone paid, deprecated, or are missing evals; sends Telegram notifications if configured
- **Webhook integration** — when you approve a proposal in the UI, llm-curator can POST the change set to any webhook endpoint (HMAC-signed). Any codebase using OpenRouter or Ollama can consume the export. See [USAGE.md](USAGE.md).

---

## What's in the stack

| Container | Role |
|---|---|
| `db` | Postgres 16 — the curator's own database (`curator`). Schema applied automatically from `migrations/` on first boot. Exposed on `127.0.0.1:5434`. |
| `curator` | Python package + system cron running the scheduled jobs. |
| `ui` | FastAPI + plain HTML dashboard (port 8088). Read/write: leaderboard, registry, proposals, alerts, settings. |

---

## Prerequisites

- Docker and Docker Compose (Compose v2+)
- An OpenRouter API key (for discovery and evals)
- Ollama running locally or on a reachable host (optional — for Ollama Cloud discovery)
- Optional: Telegram bot token + chat ID for alerts

---

## Setup

**1. Clone and configure**

```bash
git clone https://github.com/ksriram20/llm-curator.git
cd llm-curator
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```
POSTGRES_PASSWORD=<strong-password>
OPENROUTER_API_KEY=<your-key>
```

Everything else is optional. Telegram alerts and webhook delivery are silent no-ops if unconfigured.

**2. Start**

```bash
docker compose up -d
```

Postgres starts, runs migrations, then the curator and UI containers come up. Open **http://localhost:8088** — the dashboard is live.

**3. (Optional) Migrate existing data**

If you have an existing curator database, run the migration script. Read-only on the source.

```bash
SRC_DSN=postgresql://user:pass@host:port/dbname \
POSTGRES_PASSWORD=<same-as-.env> \
bash scripts/migrate_data.sh
```

---

## Schedule (IST)

| Job | Time | What it does |
|---|---|---|
| OpenRouter discovery | daily 02:00 | Fetches all OpenRouter models, upserts registry, marks deprecated |
| Ollama Cloud discovery | daily 02:30 | Scrapes Ollama Cloud, tests each model, flags paid-only |
| Price scraper | daily 03:00 | Diffs pricing vs registry; raises alerts on >5% change for in-use models |
| Eval runner | daily 04:00 + 16:00 | Picks least-recently-evaluated eligible model, runs prompts, stores scores |
| Proposal generator | Sunday 09:00 | Compares leaderboard vs live routing config, generates a proposal |

---

## CLI usage

```bash
# Registry overview
docker compose run --rm curator python -m llm_curator.cli stats

# Eval leaderboard
docker compose run --rm curator python -m llm_curator.cli leaderboard

# Leaderboard for a specific use case
docker compose run --rm curator python -m llm_curator.cli leaderboard --use-case summarization

# Show the latest proposal
docker compose run --rm curator python -m llm_curator.cli proposals

# Show alerts
docker compose run --rm curator python -m llm_curator.cli alerts

# Acknowledge an alert
docker compose run --rm curator python -m llm_curator.cli ack <id> --note "reviewed"

# Run discovery manually
docker compose run --rm curator python -m llm_curator.openrouter_discovery
docker compose run --rm curator python -m llm_curator.ollama_cloud_discovery
```

---

## Configuration reference

All variables can be set in `.env` **or** via **Settings → Configure** in the dashboard (DB values take precedence and apply immediately without restart).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `POSTGRES_PASSWORD` | Yes | — | Password for the `curator` database |
| `OPENROUTER_API_KEY` | Yes | — | Used for discovery and evals |
| `OLLAMA_URL` | No | `http://host.docker.internal:11434` | Point elsewhere if Ollama is on another machine |
| `LITELLM_CONFIG_PATH` | No | unset | Absolute host path to `litellm_config.yaml`. Mounted read-only. Enables proposal comparison against live routing config. |
| `TELEGRAM_BOT_TOKEN` | No | — | Both token and chat ID must be set for alerts to send |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat to receive alert messages |
| `WEBHOOK_URL` | No | — | Endpoint to POST proposal exports to when a proposal is applied |
| `WEBHOOK_SECRET` | No | — | HMAC-SHA256 signing secret; recipients can verify `X-LLM-Curator-Signature` |

For webhook integration details — LiteLLM YAML patching, direct OpenRouter, custom handler recipes — see [USAGE.md](USAGE.md).

---

## Graders

All graders are deterministic Python — no LLM scoring.

| Grader | Use case | Source |
|---|---|---|
| `grade_sympy` | reasoning | HELM (arXiv:2211.09110) |
| `grade_quasi_exact` | extraction | HELM |
| `grade_json_keys` | extraction | JSONSchemaBench |
| `grade_exact` | classification | — |
| `grade_ifeval_rougek` | summarization | IFEval (arXiv:2311.07911) |
| `grade_tool_call` | tool use | BFCL-inspired |
| `grade_struct_data` | structured data | StructEval (TMLR 2025) |
| `grade_code_exec` | code execution | CRUXEval (arXiv:2401.03065) |

---

## Design principles

- **Read-only advisor** — never modifies routing configs automatically; a human always approves
- **No LLM-as-judge** — all graders are deterministic Python
- **Cost-gated** — hard $0.10 cap per eval run; paid models require explicit opt-in
- **Provider-agnostic** — proposals export to a standard JSON schema consumable by any stack
- **Lean** — Python scripts and cron, no heavy orchestration frameworks
