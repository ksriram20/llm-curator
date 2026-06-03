# llm-curator — Architecture

> This document describes the system design, components, and data flow.
> It is versioned alongside the codebase and updated with each meaningful change.

---

## Version: v0.2 (current design target)

---

## Purpose

llm-curator is a self-hosted platform that continuously tracks LLM models across providers — which are free, which have gone paid, which are deprecated — evaluates them deterministically, generates human-readable capability reports, and surfaces routing proposals for any LiteLLM-based system.

It never auto-applies anything. It informs; a human or a downstream LLM decides.

---

## Containers

```
┌─────────────────────────────────────────────────────┐
│  docker-compose                                      │
│                                                      │
│  ┌──────────────┐     ┌───────────────────────────┐ │
│  │     db       │     │        curator            │ │
│  │ Postgres 16  │◄────│  Python package + cron    │ │
│  │ port 5434    │     │  4 scheduled jobs         │ │
│  └──────────────┘     └───────────────────────────┘ │
│                                                      │
│  ┌───────────────────────────────────────────────┐  │
│  │  price-scraper  (planned v0.2)                │  │
│  │  Scrapes official pricing pages, updates      │  │
│  │  pricing_input/output in llm_registry         │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## Scheduled Jobs (v0.1 times — to be revised in v0.2)

| Job | Schedule (IST) | Module | What it does |
|---|---|---|---|
| Eval runner | daily 02:37 | `eval_runner.py` | Picks oldest-unevaluated free model, runs all prompts, stores scores |
| OpenRouter discovery | daily 03:17 | `openrouter_discovery.py` | Fetches all OpenRouter models, upserts registry, marks deprecated |
| Ollama Cloud discovery | daily 03:43 | `ollama_cloud_discovery.py` | Scrapes Ollama Cloud, tests each model, flags paid-only |
| Proposal generator | Sundays 09:23 | `proposal_generator.py` | Compares leaderboard vs live LiteLLM config, writes proposal |

> Note: Odd scheduling times were inherited from the original host application to avoid job conflicts. Now standalone, these will be rationalised in v0.2.

---

## Python Package — Module Map

```
llm_curator/
├── db.py                    # Postgres connection pool, cursor context manager
├── policy.py                # is_eval_eligible() — rules for which models can be evaluated
│
├── openrouter_discovery.py  # Crawls OpenRouter API, upserts llm_registry
├── ollama_cloud_discovery.py# Scrapes Ollama Cloud, upserts llm_registry
│
├── eval_prompts.py          # Fixed prompt definitions + deterministic graders
├── eval_providers.py        # call() abstraction over OpenRouter + Ollama APIs
├── eval_runner.py           # Orchestrates one model eval: pick → run → grade → persist
│
├── proposal_generator.py    # Weekly diff: leaderboard vs litellm_config.yaml
├── alert_detector.py        # Detects in-use models gone paid/deprecated/unevaluated
├── litellm_config_parser.py # Reads litellm_config.yaml from host router (read-only, optional)
├── sync_litellm_flag.py     # Syncs in_litellm flag in registry from config file
│
└── cli.py                   # CLI: stats, leaderboard, proposals, alerts, ack
```

---

## Database Schema (Postgres — `curator` database)

### `llm_registry`
Central catalog of all known models across all sources.

| Column | Type | Notes |
|---|---|---|
| `model_id` | TEXT | e.g. `deepseek/deepseek-v4-flash` |
| `source` | TEXT | `openrouter` / `ollama-cloud` / future providers |
| `provider` | TEXT | Organisation name |
| `is_free` | BOOLEAN | Current pricing status |
| `in_litellm` | BOOLEAN | Whether active in LiteLLM routing config |
| `pricing_input` | NUMERIC | Per-token input price (updated by price-scraper in v0.2) |
| `pricing_output` | NUMERIC | Per-token output price |
| `deprecated` | BOOLEAN | Set after 30-day absence from discovery |
| `first_seen` / `last_seen` | TIMESTAMPTZ | Lifecycle tracking |

### `llm_evals`
One row per model per prompt per eval run.

| Column | Notes |
|---|---|
| `use_case` | `reasoning` / `extraction` / `classification` / `summarization` |
| `eval_name` | Prompt slug (e.g. `reasoning_widgets`) |
| `score` | 0.0–1.0, deterministic grader output |
| `raw_output` | Model's actual response (truncated at 4000 chars) |
| `latency_ms` | Wall-clock response time |
| `cost_usd` | Computed from token counts × pricing |
| `grader_version` | *(planned v0.2)* — version of grading suite used |

### `llm_proposals`
Weekly proposal records from `proposal_generator.py`.

### `llm_alerts`
Active alerts: in-use models with issues needing attention.

### `llm_discovery_runs`
Audit trail: one row per discovery job execution.

---

## Evaluation Pipeline (v0.1)

```
pick_next_model()
    │  ORDER BY last_evaluated_at NULLS FIRST
    │  WHERE is_free=TRUE, deprecated=FALSE
    │  policy.is_eval_eligible() filter
    ▼
projected_cost() check → skip if > $0.10
    ▼
for each prompt in PROMPTS (6 total):
    eval_providers.call(model_id, source, user, system)
        ├── OpenRouter → POST /v1/chat/completions
        └── Ollama     → POST /api/generate
    ▼
    prompt["grader"](output, expected) → score ∈ [0.0, 1.0]
    record_eval() → INSERT INTO llm_evals
    ▼
mean_score = avg(non-None scores)
touch_last_evaluated() → UPDATE llm_registry
notify() → Telegram shim
```

### Graders (v0.1 — deterministic, no LLM-as-judge)

| Grader | Used for | Logic |
|---|---|---|
| `grade_integer` | reasoning | Extracts first integer, compares to expected |
| `grade_json_keys` | extraction | Validates required JSON keys present and correct |
| `grade_exact` | classification | Case-insensitive exact string match |
| `grade_length` | summarization | Word count within allowed bounds |

> v0.2 will replace/augment these based on research findings in `docs/research/`.

---

## LLM Report Files (planned v0.2)

Each evaluated model gets a markdown file at:
```
llms/<source>/<model-slug>.md
```
e.g. `llms/openrouter/deepseek--deepseek-v4-flash.md`

The file is auto-generated after each eval run:
1. Deterministic grader scores populate the data section
2. An LLM (configured via `REPORT_MODEL` env var) writes a narrative capability summary
3. The file is committed or written to disk — not stored only in the DB

This file is the primary interface between llm-curator and the host router's routing decisions.

---

## Host Router Integration (v0.2 output path)

```
llm_curator eval run
    ▼
llms/openrouter/<model>.md  (narrative report)
    +
proposals/<date>.json        (structured routing diff)
    ▼
Host router reads these weekly
    ▼
LiteLLM config updated (human-reviewed or LLM-assisted)
```

---

## Provider Adapter Interface (planned v0.2)

Goal: allow any provider to be plugged in by implementing two functions:

```python
def discover() -> list[ModelRecord]:
    """Fetch all models from the provider. Return standardised records."""

def call(model_id, user_prompt, system_prompt, max_tokens) -> CallResult:
    """Send one completion request. Return output, tokens, latency, error."""
```

Built-in adapters: `openrouter`, `ollama-cloud`
Community adapters: any provider following this interface

---

## Pricing Scraper (planned v0.2)

New container `price-scraper`:
- Crawls official pricing pages for known providers on a schedule
- Parses current `pricing_input` / `pricing_output` per model
- UPSERTs into `llm_registry`
- Raises an alert if pricing changes for any `in_litellm=TRUE` model

---

## Key Design Principles

1. **Read-only advisor** — never modifies routing configs automatically
2. **No LLM-as-judge** — graders are deterministic code; an LLM narrates results but does not score them
3. **Cost-gated** — hard cap per eval run; paid models require explicit opt-in
4. **Provider-agnostic** — OpenRouter and Ollama today; adapter interface for any provider
5. **Lean** — Python scripts over heavy orchestration frameworks (no n8n, no Airflow)
6. **Auditable** — every eval run, discovery run, and proposal stored with full provenance

---

*Last updated: 2026-06-03 | Version: v0.2 design*
