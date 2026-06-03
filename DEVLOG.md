# llm-curator — Development Log

> Chronological record of decisions, pivots, and rationale during development.
> Add an entry whenever a meaningful architectural or design decision is made.
> Format: `## YYYY-MM-DD — <short title>`

---

## 2026-06-03 — Project inception as standalone repo

**Context:** llm-curator was originally part of PARCON (`parcon/sahay/llm_curator/`), running as systemd timers inside the PARCON stack and sharing PARCON's Postgres database.

**Decision:** Extract into a fully standalone repo with its own Postgres instance, own Docker stack, and own cron scheduler.

**Rationale:**
- PARCON's database and scheduler created hidden coupling — llm-curator jobs were scheduled at odd hours (02:37, 03:17, 03:43 IST) specifically to avoid interfering with PARCON workloads
- A standalone service can evolve independently, be open-sourced, and be used by anyone without requiring PARCON
- The only optional link back to PARCON is a read-only mount of `litellm_config.yaml` for proposal comparison; unset `PARCON_LITELLM_CONFIG` and there is zero dependency

**What was carried over:** Python package verbatim (no code edits needed — all coupling was env-driven). Four migration files. Crontab and Dockerfile adapted for standalone operation.

---

## 2026-06-03 — Evaluation mechanism review

**Context:** Initial eval suite uses 6 fixed prompts with shallow deterministic graders (exact match, integer extraction, JSON key check, word count).

**Decision:** Keep deterministic grading as the core principle; upgrade grader quality; add LLM-written narrative layer on top of grader output.

**Rationale:**
- LLM-as-judge introduces bias (judge models favor responses similar to their own training), potential hallucination in scoring, and a circular dependency (using a known model to evaluate unknown ones)
- Current graders are correct in principle but too shallow — `grade_exact` fails correct answers with extra words; `grade_length` is a word count not a quality check
- The right fix is better deterministic graders (richer rubrics, structured output validation, multi-step verification), not replacing them with an LLM judge
- An LLM is appropriate for *narrating* grader output into a human-readable capability report — it is summarising data it did not produce, not making quality judgements

**Action:** Commission Gemini Deep Research pass on arXiv/NLP literature for grading method candidates. Output stored in `docs/research/`. Graders to be upgraded in v0.2 with a `grader_version` column added to `llm_evals`.

---

## 2026-06-03 — Competitive landscape review

**Finding:** No existing open-source tool combines llm-curator's specific feature set:
- Daily model discovery across providers (free/paid/deprecated tracking)
- Automated eval pipeline with deterministic graders
- Postgres model registry
- Routing config proposal generation
- Deprecation and pricing-change alerts

**Closest tools and why they don't cover the same ground:**
- `promptfoo` (21k stars) — eval quality for prompts you own; no discovery, no registry, no routing proposals
- `lm-evaluation-harness` (12k stars) — academic benchmarking; research-focused, not operational
- `openai/evals` (18k stars) — eval framework; no model availability or pricing tracking
- Various LiteLLM stacks on GitHub — static configs, no eval, no discovery loop

**Conclusion:** llm-curator fills a gap between eval frameworks (assume you know which model to use) and routing proxies (assume someone else picked the models).

---

## 2026-06-03 — v0.2 scope defined

**Scope agreed:**

1. **Pricing scraper container** — new `price-scraper` service that crawls official provider pricing pages and keeps `pricing_input`/`pricing_output` current in `llm_registry`. Alerts on pricing changes for in-use models.

2. **Documentation harness** — `docs/ARCHITECTURE.md`, `STATUS.md`, `DEVLOG.md` (this file), `llms/<source>/<model>.md` report files with standardised template.

3. **Grader upgrade** — research-backed replacement of shallow graders; `grader_version` column on `llm_evals`; monthly versioning cycle.

4. **LLM narrative reports** — after each eval run, an LLM generates a capability writeup for the model; stored as `llms/<source>/<model>.md`; serves as the primary interface to PARCON routing decisions.

5. **Provider adapter interface** — formalise `discover()` + `call()` contract so any provider can be plugged in.

6. **Paid model support** — extend eval to OpenRouter paid models with per-model cost cap overrides; tiered eval depth (light vs full suite).

7. **Schedule rationalisation** — odd IST hours no longer needed; redesign cron schedule for standalone operation.

**Deferred:**
- n8n workflow (lean Python scripts preferred)
- Public leaderboard UI (v0.3)
- Community result submission (v0.3)

---

*Add new entries above this line, newest first.*
