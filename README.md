# llm-curator 🐙

A self-contained service that discovers, evaluates, and recommends LLMs — and
keeps a registry of which models are free, paid, or deprecated across providers
(OpenRouter, Ollama Cloud). It compares its eval-based recommendations against a
LiteLLM routing config and surfaces **proposals** (it never auto-applies them).

Extracted from PARCON into its own repo and its own Docker stack. It has **no
runtime dependency on PARCON** — it ships its own Postgres and scheduler. The
only optional link is a read-only view of PARCON's `litellm_config.yaml`.

## What's in the stack

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:16-alpine` | The curator's own database (`llm_curator`). Schema auto-applied from `migrations/` on first boot. Exposed on `127.0.0.1:5434`. |
| `curator` | built from `Dockerfile` | The Python package + `supercronic` scheduler running the four jobs. |

## Quick start

```bash
cp .env.example .env      # fill in POSTGRES_PASSWORD, OPENROUTER_API_KEY, etc.
docker compose up -d db   # creates the DB + schema
./scripts/migrate_data.sh # (optional) copy existing data out of PARCON, read-only
docker compose up -d      # start the scheduler
```

One-shot CLI (no scheduler):

```bash
docker compose run --rm curator python -m llm_curator.cli stats
docker compose run --rm curator python -m llm_curator.cli leaderboard
```

## Schedule (IST, mirrors the old PARCON timers)

| Job | When | Module |
|---|---|---|
| OpenRouter discovery | daily 03:17 | `openrouter_discovery` |
| Ollama Cloud discovery | daily 03:43 | `ollama_cloud_discovery` |
| Daily eval (1 model) | daily 02:37 | `eval_runner` |
| Proposal regen | Sun 09:23 | `proposal_generator` |

Defined in `crontab`. Container TZ is `Asia/Kolkata`.

## Configuration

All via `.env` (see `.env.example`). Everything PARCON-specific is a plain env
var or an optional read-only mount — there is no hardcoded PARCON path or import.

- `CSR_DSN` — set by compose to the bundled `db`. The package reads this verbatim.
- `LITELLM_CONFIG_PATH` — mounted to `/config/litellm_config.yaml` from
  `PARCON_LITELLM_CONFIG`. Unset it to run with zero PARCON contact.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — optional; alerts no-op if absent.

## Relationship to PARCON

This repo was carved out of `parcon/sahay/llm_curator/`. The package code is a
**verbatim copy** — no edits were needed, because every coupling point was
already env-driven (`CSR_DSN`, `LITELLM_CONFIG_PATH`) or a soft no-op import
(`memory_notify`). See `docs/proposals/llm-curator-extraction.md` in PARCON for
the staged plan. PARCON still holds the original until the cutover (Stage 2/3) is
verified.
