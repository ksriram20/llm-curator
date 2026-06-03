# Prompt — Build the LLM-Curator properly (first principles → v2-or-refactor → plan)

> Copy everything below the line into a fresh Claude Code session started in the
> **llm-curator** repo (`/home/sriram/llm-curator/`). It is self-contained. Its job
> is to get the *foundations* right, then decide whether to build v2 from scratch or
> refactor — NOT to rush an implementation.

---

You are working in the **LLM-Curator** repo at `/home/sriram/llm-curator/`. It used
to live inside PARCON (`/home/sriram/parcon/`) and has been deliberately detached so
it can grow as a **standalone, reusable service**. Your job this session is to **get
the basics and first principles right**, assess what already exists, then recommend
**v2-from-scratch vs refactor**, and produce a build plan. Do **not** mass-implement
yet — foundations and contract first.

## Mission (one sentence)
Continuously and credibly answer: *"For a given task-profile, under cost/latency/
context constraints, which model available right now is best — with a ranked
fallback chain?"* — and **publish that answer as a stable, machine-readable contract**
that downstream routers (first consumer: PARCON's Research Vertical LiteLLM router)
can plug straight in.

## Step 0 — Understand what exists
Read the current llm-curator repo end to end before proposing anything. Known
history (verify against the actual code): a Phase-1 existed that did **discovery +
registry** — `openrouter_discovery.py` (fetched ~357 OpenRouter models), an Ollama
cloud discovery, and Postgres tables `llm_registry`, `llm_evals`, `llm_discovery_runs`
(these were in PARCON's `parcon_csr`; confirm where they live now). Map what's solid,
what's stubbed, and what's entangled with PARCON.

## First principles (establish these concretely, grounded in the repo + a little research)
1. **One question, asked continuously.** Everything serves answering the mission
   question credibly and keeping the answer fresh. A dashboard is a *view*, not the product.
2. **Models are not fungible — rank per *task-profile*, never globally.** Define a
   small, explicit set of profiles, e.g. `fast-structured`, `quality-drafting`,
   `reasoning`, `long-context`, maybe `vision`/`coding`. "Best" is always per-profile.
3. **Blend three evidence sources — and weight reliability as co-equal with capability:**
   - **Capability** — external benchmarks/leaderboards + your *own* small task-profile
     eval sets (run candidates, score outputs, optionally with a judge model).
   - **Operational reliability** — *measured by actually calling the models*:
     empty-response rate, error/5xx rate, latency p50/p95, rate-limit (429) frequency.
     (Hard-won lesson from the consumer side: a "smart" model that intermittently
     returns an empty 200 is worse than a dumber model that always answers. Reliability
     must be a first-class ranking signal, not an afterthought.)
   - **Economics** — price per M tokens (input/output), free-tier availability and its
     rate limits, provider.
4. **Evaluate; don't just trust leaderboards.** Maintain small, representative,
   per-profile eval sets with automatic scoring. Re-run on a cadence. Community signals
   (OpenRouter usage rankings, public leaderboards) are *inputs*, not the verdict.
5. **Freshness/decay is intrinsic.** Models appear, deprecate, and reprice constantly.
   Discovery + re-eval run on a schedule; every recommendation carries a `generated_at`
   and a confidence; stale signals decay.
6. **The product is a contract, not a UI.** The primary output is a versioned,
   machine-readable recommendation per profile (primary + ranked fallbacks + the
   metadata a router needs). Consumers subscribe to it; the curator never reaches into
   a consumer.
7. **Economical by construction.** Evals cost money. Use cheap/free models as judges
   where adequate, cache eval results, *sample* rather than exhaustively test, probe
   via free tiers, and bound spend. The curator must not become a money pit.
8. **Decoupled and reusable.** Zero hard dependency on PARCON. The only coupling is the
   published contract (a file, an HTTP endpoint, or a DB table the consumer reads).
9. **Auditable.** Every recommendation traces back to the discovery snapshot + eval
   runs that produced it.

## The output contract (north star — design this carefully)
A stable, versioned `recommendations` artifact, roughly:
```jsonc
{
  "schema_version": "1.0",
  "generated_at": "2026-06-02T...Z",
  "profiles": {
    "fast-structured": {
      "primary":  "deepseek/deepseek-v4-flash:free",
      "fallbacks": ["openrouter:deepseek/deepseek-v4-flash", "deepseek-official:deepseek-v4-flash"],
      "scores": { "capability": 0.0, "reliability": 0.0, "blended": 0.0 },
      "cost_per_m": { "in": 0.0, "out": 0.0 },
      "provider": "...", "context": 0, "confidence": 0.0
    },
    "quality-drafting": { ... },
    "reasoning": { ... }
  }
}
```
It must be **sufficient on its own** to drive a LiteLLM router: a consumer reads it and
sets each tier's primary + fallback chain with no extra judgement. Map curator profiles
→ consumer tiers (PARCON RV uses `rv-fast` / `rv-quality` / `rv-reason`). Publish via a
mechanism a consumer can poll/pull (file in a known path, small read-only API, or a DB
table). Keep last-known-good so a bad refresh can't break consumers.

## v2-from-scratch vs refactor — decide explicitly
After reading the repo, recommend one, with reasons:
- **Refactor/extend** if Phase-1 discovery + registry is sound and the real gaps are
  *evaluation, reliability measurement, ranking, and the output contract*.
- **v2 from scratch** if the existing foundations fight the first principles above
  (e.g. the schema can't express per-profile reliability, or it's entangled with
  PARCON/its Dashboard such that decoupling is more work than rebuilding).
Be honest; do not default to "rewrite" for novelty.

## Deliverables (this session)
1. **`FIRST-PRINCIPLES.md`** — the principles above, refined and grounded in the repo.
2. **Assessment** of the existing curator: what exists, what's reusable, what's entangled.
3. **A v2-vs-refactor recommendation** with rationale.
4. **The output-contract spec** (the recommendation schema + publish/consume mechanism +
   profile↔tier mapping for PARCON RV).
5. **A phased build plan** (discovery → reliability probes → eval harness → ranking →
   contract publisher → scheduler), with cost guardrails.

## Constraints
- **Standalone & decoupled.** No imports from PARCON. Communicate only via the contract.
- **OpenRouter-first** (one key, many models) is the assumed primary gateway; keep
  provider-diversity in mind for fallback resilience. Track free vs paid + rate limits.
- **Economical.** Bound eval spend; cache; sample; prefer free tiers for probing.
- **Don't over-build now.** The goal is correct foundations + a crisp contract + a plan.
  Implementation proceeds in the next session(s).

## Note for later (separate session, separate repo)
The *consumer* side — wiring this contract into PARCON RV's LiteLLM router (tiered
aliases, fallback chains, provenance) — is specified in PARCON at
`docs/rv/llm-curator-rv-integration-PROMPT.md`. Build the curator's contract first;
the RV side consumes it.

Start by reading the existing llm-curator repo, then write `FIRST-PRINCIPLES.md` and the
v2-vs-refactor recommendation before any implementation.
