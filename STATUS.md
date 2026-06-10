# llm-curator — STATUS

> Version tracking, completed work, and proposed roadmap.
> Updated manually after each meaningful change. Keep entries brief and factual.

---

## Current Version: v0.4

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

### Scheduling (inherited from host app)
- [x] Eval runner: daily 02:37 IST
- [x] OpenRouter discovery: daily 03:17 IST
- [x] Ollama Cloud discovery: daily 03:43 IST
- [x] Proposal generator: Sundays 09:23 IST

---

## v0.2 — Completed

### Architecture & Documentation
- [x] `docs/ARCHITECTURE.md` — harness overview, containers, DB schema, data flow
- [x] `STATUS.md` (this file) — maintained going forward
- [x] `DEVLOG.md` — chronological development decisions log

### Evaluation Improvements
- [x] Research-backed grader upgrade — `grade_sympy`, `grade_json_doc`, `grade_quasi_exact`, `grade_ifeval_rougek`
- [x] `grader_version` column on `llm_evals` (migration `05_grader_version.sql`); `GRADER_VERSION = "v2"`
- [x] Sources: HELM (arXiv:2211.09110), JSONSchemaBench, IFEval (arXiv:2311.07911), ROUGE-K

### Dashboard
- [x] `ui` container — FastAPI + plain HTML read-only dashboard (port 8088)
- [x] 5 pages: Dashboard, Registry, Leaderboard, Proposals, Alerts

### Hygiene
- [x] All PARCON references removed; fully standalone public repo
- [x] DB user corrected to `parcon` (curator_user never applied to existing volume)

---

## v0.3 — In Progress

All items below are v0.3 scope. Items 2–5 were originally proposed in v0.2 but deferred.

### 1. Tool Use Grader (5th use case) — NEW
- [x] Migration `06_tool_use.sql` — adds `tool_use` to `use_case` CHECK constraint
- [x] `call_with_tools()` in `eval_providers.py` — OpenRouter function-calling API format
- [x] `grade_tool_call()` grader — BFCL-inspired deterministic JSON comparison; `GRADER_VERSION = "v3"`
- [x] 5 canary prompts: simple, distractor, abstain, schema-strict, implicit-args
- [x] `eval_runner.py` — routes `tool_use` prompts to `call_with_tools()`; Ollama short-circuits with error

### 2. Formal Provider Adapter Interface — CARRIED FROM v0.2
- [x] `ProviderAdapter` Protocol in `eval_providers.py` — typed contract for `call()` + `call_with_tools()`
- [x] `ToolCallResult` dataclass with `.to_call_result()` bridge
- [x] `ADAPTER_SOURCES` registry; new providers plug in without touching eval logic

### 3. Pricing Scraper — CARRIED FROM v0.2
- [x] `price_scraper.py` — fetches current pricing from OpenRouter `/api/v1/models`
- [x] Diffs against `llm_registry.pricing_input / pricing_output`; raises `llm_alerts` on >5% change for `in_litellm` models
- [x] Stubs for Mistral, Deepseek, Google AI Studio (graceful degradation)
- [ ] Schedule wiring (pending Item 6 — schedule rationalisation)

### 4. Paid Model Eval Support — CARRIED FROM v0.2
- [x] Migration `07_eval_tiers.sql` — adds `eval_cost_cap_usd` to `llm_registry`
- [x] Tiered eval depth: light (2 prompts) for never-evaluated models; full suite otherwise
- [x] Per-model cost cap override; rotation picker includes paid models with explicit cap set

### 5. Schedule Rationalisation — CARRIED FROM v0.2
- [x] Fixed ordering bug: discovery now runs before eval runner (was inverted)
- [x] Added price scraper slot between discovery and eval
- [x] Eval runner runs twice daily (04:00 + 16:00 IST) — 2 models/day
- [x] All times rounded to clean IST hours; odd inherited times removed
- [x] New schedule: 02:00 discovery → 02:30 Ollama → 03:00 pricing → 04:00/16:00 eval → Sun 09:00 proposals

---

---

## v0.4 — Completed

Two new deterministic graders + end-to-end wiring (leaderboard, migration, UI).

### 1. grade_struct_data — StructEval (TMLR 2025)
- [x] Two-stage: syntax_ok (0.40) + key_validation (0.60)
- [x] Formats: YAML (`yaml.safe_load`), XML (`xml.etree.ElementTree`), CSV (`csv.DictReader`)
- [x] Dot-path traversal for nested keys (`services.web.image`)
- [x] 3 prompts: `struct_yaml_config`, `struct_xml_record`, `struct_csv_report`

### 2. grade_code_exec — CRUXEval (arXiv:2401.03065)
- [x] Strips markdown fences, builds test harness, runs via `subprocess` (5s timeout)
- [x] Score = passed_cases / total_cases
- [x] Runs inside existing curator container — no extra infrastructure
- [x] 3 prompts: `code_list_filter`, `code_string_reverse`, `code_fizzbuzz`

### 3. Infrastructure
- [x] `GRADER_VERSION = "v4"` (v3 scores preserved in DB)
- [x] `migrations/09_v4_use_cases.sql` — extends CHECK constraint with `structured_data`, `code_exec`
- [x] `leaderboard.py` — adds `tool_use`, `structured_data`, `code_exec` columns; default version → v4
- [x] `leaderboard.html` — 3 new columns, version filter updated to v4
- [x] `db.py` — fixed idle-in-transaction bug: `cursor()` now a proper context manager (commit/rollback)
- [x] Migrations 06 + 07 applied to live DB (were missing, causing eval_runner crash since v0.3 deploy)

---

## Deferred (post-v0.4)

- UI redesign ("Claude Design" — full visual refresh)
- LLM-written narrative reports per model (`llms/<source>/<model>.md`)
- Public leaderboard web UI
- Community result submission (standardised schema + provenance)
- Multi-region latency benchmarking
- Host router structured JSON/YAML export

---

*Last updated: 2026-06-10 | Maintainer: Sriram*
