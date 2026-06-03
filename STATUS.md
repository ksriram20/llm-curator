# llm-curator — STATUS

> Version tracking, completed work, and proposed roadmap.
> Updated manually after each meaningful change. Keep entries brief and factual.

---

## Current Version: v0.1 (baseline)

Extracted from a private internal application. Standalone service, own Postgres, own scheduler.

---

## v0.1 — Completed

### Infrastructure
- [x] Dockerized stack: `db` (Postgres 16, port 5434) + `curator` (Python + cron)
- [x] Auto-migration on first boot from `migrations/` folder
- [x] `scripts/migrate_data.sh` for one-time data import from an existing source DB
- [x] `.env`-driven config with sensible defaults; no hardcoded secrets

### Discovery
- [x] OpenRouter discovery (`openrouter_discovery.py`) — daily crawl, upserts registry, marks deprecated after 30-day absence
- [x] Ollama Cloud discovery (`ollama_cloud_discovery.py`) — scrapes cloud models, tests each, flags paid-only
- [x] `llm_discovery_runs` audit trail per run

### Evaluation
- [x] 6 fixed eval prompts across 4 use cases: reasoning (×2), extraction (×1), classification (×2), summarization (×1)
- [x] Deterministic graders: `grade_integer`, `grade_json_keys`, `grade_exact`, `grade_length`
- [x] Free-models-only by default; `--include-paid` flag for manual override
- [x] Hard cost cap: $0.10 per model per eval run
- [x] One model per day rotation (oldest-evaluated-first)
- [x] Results stored in `llm_evals` with raw output, latency, tokens, cost

### Proposals & Alerts
- [x] Weekly proposal generator (Sundays) — compares leaderboard vs live `litellm_config.yaml`
- [x] Alert detector — flags in-use models gone paid, deprecated, or missing evals
- [x] Telegram notification shim (silent no-op if unconfigured)

### CLI
- [x] `stats`, `leaderboard`, `proposals`, `alerts`, `ack` commands via `python -m llm_curator.cli`

### Scheduling (inherited from host app — to be revisited in v0.2)
- [x] Eval runner: daily 02:37 IST
- [x] OpenRouter discovery: daily 03:17 IST
- [x] Ollama Cloud discovery: daily 03:43 IST
- [x] Proposal generator: Sundays 09:23 IST

---

## v0.2 — Proposed

### Architecture & Documentation
- [ ] `docs/ARCHITECTURE.md` — harness overview, components, data flow (created in v0.2)
- [ ] `STATUS.md` (this file) — maintained going forward
- [ ] `DEVLOG.md` — chronological development decisions log
- [ ] `llms/<source>/<model>.md` — per-model eval report files with standardised template
- [ ] Move `CURATOR_INTERNALS.md` into `docs/`

### Pricing Scraper (new container)
- [ ] New `price-scraper` container that crawls official provider pricing pages
- [ ] Updates `pricing_input` / `pricing_output` in `llm_registry` on a schedule
- [ ] Targets: OpenRouter pricing page, Ollama Cloud, Mistral, Deepseek, Google AI Studio
- [ ] Triggers alert if pricing changes for an in-use model

### Evaluation Improvements
- [ ] Research-backed grader upgrade (from Gemini Deep Research output in `docs/research/`)
- [ ] Add `grader_version` column to `llm_evals` table (migration `05_grader_version.sql`)
- [ ] Monthly grader versioning cycle — new graders get a version bump, old scores preserved
- [ ] LLM-written narrative report per model — generated after each eval run, stored as `llms/<source>/<model>.md`
- [ ] Tiered eval depth: light (2 prompts) for discovery, full suite for routing candidates

### Provider Extensibility
- [ ] Formal provider adapter interface — `discover()` + `call()` contract
- [ ] Support paid models from OpenRouter with per-model cost cap overrides
- [ ] Design for community-contributed provider adapters (beyond OpenRouter + Ollama)

### Scheduling (standalone cleanup)
- [ ] Revisit cron schedule now that odd-hours avoidance (host app conflict) no longer applies
- [ ] Consider running discovery more frequently for newly added sources

### Host Router Integration (output side)
- [ ] Structured JSON/YAML export from proposal generator for LiteLLM to consume
- [ ] Host router reads `llms/<source>/<model>.md` reports + proposal JSON weekly to update routing

---

## v0.3 — Ideas (not yet scoped)

- [ ] Public leaderboard web UI
- [ ] Community result submission (standardised schema + provenance fields)
- [ ] Multi-region latency benchmarking
- [ ] Tool-use eval prompts (function calling quality)
- [ ] n8n workflow (deferred — lean Python scripts preferred for now)

---

*Last updated: 2026-06-03 | Maintainer: Sriram*
