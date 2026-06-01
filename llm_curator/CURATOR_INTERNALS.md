# llm_curator — PARCON LLM discovery + curation

**Purpose:** Keep a comprehensive registry of all known LLMs (across providers) so the LiteLLM config can be regenerated from evidence, not memory.

## Components

| File | Role |
|---|---|
| `db.py` | psycopg2 helper (lazy reconnect; uses `CSR_DSN`) |
| `openrouter_discovery.py` | Daily: fetches all OpenRouter models, upserts into `llm_registry` |
| `ollama_cloud_discovery.py` | Daily: scrapes Ollama Cloud catalog + verifies each via local Ollama test call. Free vs paid is **observed**, not assumed. |
| `ollama_seed_known_free.txt` | Sriram's manually-verified Ollama Cloud free-tier list (seed source of truth) |
| `cli.py` | Inspection CLI (`stats`, `list`, `show`, `runs`, `in-litellm`) |

## Database (parcon_csr)

- `llm_registry` — one row per model per source. Stores capabilities, pricing, modalities, freshness.
- `llm_evals` — Phase 2 will populate this with graded performance per use case.
- `llm_discovery_runs` — audit trail of every discovery run.

## Phase status

- **Phase 1 (DONE)**: Discovery agents + registry DB + CLI.
- **Phase 2 (DONE)**: Eval agent — `eval_runner.py` rotates one model/day, populates `llm_evals`. Free-only by default; `--include-paid` overrides. Hard $0.10 cost cap per run.
- **Phase 3 (DONE)**: Curator agent — `proposal_generator.py` runs weekly (Sun 09:23), reads `llm_evals`, produces structured proposals in `llm_proposals`. NEVER auto-applies. Honest behaviour with sparse data: refuses to recommend REPLACE unless both incumbent AND candidate have ≥2 recent evals AND candidate beats by ≥0.10. Surfaces unevaluated aliases as "needs eval data" for prioritisation in the rotation queue.
- **Phase 4 (DONE)**: Alerts — `alert_detector.py` runs at the end of each discovery cycle. Detects IN_USE_DEPRECATED (in-litellm model just deprecated), IN_USE_PAID (Ollama Cloud in-use model moved free→paid), LITELLM_ORPHAN (alias points at model not in registry). Critical alerts get Telegram-relayed via memory_notify; all alerts land in dashboard `/llm-curator` widget. 24h dedup window prevents spam. `sync_litellm_flag.py` keeps in_litellm/litellm_alias columns synced with the live YAML config.

## CLI examples

```bash
cd /home/sriram/parcon/sahay
./brain/venv/bin/python -m llm_curator.cli stats
./brain/venv/bin/python -m llm_curator.cli list --source ollama-cloud --free
./brain/venv/bin/python -m llm_curator.cli list --source openrouter --free --limit 30
./brain/venv/bin/python -m llm_curator.cli show deepseek/deepseek-v4-flash
./brain/venv/bin/python -m llm_curator.cli runs --limit 10
./brain/venv/bin/python -m llm_curator.cli evals --limit 20
./brain/venv/bin/python -m llm_curator.cli leaderboard
./brain/venv/bin/python -m llm_curator.cli leaderboard --use-case reasoning

# Phase 3 — curator proposals
./brain/venv/bin/python -m llm_curator.cli propose                # dry-run preview
./brain/venv/bin/python -m llm_curator.cli propose --persist      # persist + Telegram
./brain/venv/bin/python -m llm_curator.cli proposals              # list recent
./brain/venv/bin/python -m llm_curator.cli proposal 1             # full detail of #1

# Phase 4 — alerts
./brain/venv/bin/python -m llm_curator.cli alerts                 # unacknowledged
./brain/venv/bin/python -m llm_curator.cli alerts --all           # include acked
./brain/venv/bin/python -m llm_curator.cli alerts --severity critical
./brain/venv/bin/python -m llm_curator.cli ack 1 --note "fixed in litellm_config v1.4"
./brain/venv/bin/python -m llm_curator.sync_litellm_flag          # manually re-sync flags
./brain/venv/bin/python -m llm_curator.alert_detector             # manually scan
```

## Manual eval run

```bash
cd /home/sriram/parcon/sahay
# Pick next-due free model and evaluate
./brain/venv/bin/python -m llm_curator.eval_runner
# Evaluate a specific model
./brain/venv/bin/python -m llm_curator.eval_runner --model gpt-oss:120b-cloud --source ollama-cloud
# Override the free-only safety net (only for manual runs)
./brain/venv/bin/python -m llm_curator.eval_runner --include-paid
```

## Scheduling

See `docker/llm-curator/README.md` for systemd timer install instructions.
- OpenRouter: 03:17 daily
- Ollama Cloud: 03:43 daily
