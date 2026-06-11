# llm-curator — Integration Guide

llm-curator is a self-hosted LLM evaluation platform that runs deterministic benchmarks
against models on **OpenRouter** and **Ollama Cloud** and generates weekly routing
proposals. It is provider-agnostic: any codebase that calls OpenRouter or Ollama can
consume its proposals regardless of how routing is implemented.

---

## Quick Start

```bash
git clone https://github.com/ksriram20/llm-curator
cd llm-curator
cp .env.example .env          # add OPENROUTER_API_KEY at minimum
docker compose up -d
open http://localhost:8088
```

Open **Settings** and paste your OpenRouter API key. The evaluator runs automatically
on a cron schedule (04:00 + 16:00 IST); trigger manually with:

```bash
docker compose exec curator python -m llm_curator.eval_runner
```

---

## Understanding Proposals

Proposals are generated weekly (Sunday 09:00 IST) by comparing eval scores against
your current LiteLLM routing config. Each proposal contains:

| Field | Meaning |
|---|---|
| `changes` | Actionable routing edits (replace / add / remove) |
| `needs_eval` | Aliases with insufficient eval data |
| `status` | `pending` → `applied` or `rejected` |

View proposals at **http://localhost:8088/proposals.html**.

---

## Integrating Proposals into Your Codebase

### Option 1 — Export JSON and apply manually

Every proposal has a clean export endpoint:

```
GET http://localhost:8088/api/proposals/{id}/export
```

Response:
```json
{
  "schema_version": "1.0",
  "proposal_id": 3,
  "generated_at": "2026-06-10T...",
  "summary": "1 replacement, 0 additions",
  "changes": [
    {
      "kind": "replace",
      "alias": "reasoning",
      "from_model": "openai/gpt-oss-20b:free",
      "to_model":   "openai/gpt-oss-120b:free",
      "rationale":  "Score improved 0.986 → 1.000 over 17 evals",
      "evidence":   {"new_score": 1.0, "n_evals": 17}
    }
  ],
  "needs_eval": []
}
```

You can also download the JSON directly from the Proposals UI (Export JSON button).

---

### Option 2 — Webhook (recommended for automation)

Configure a webhook URL in **Settings → Integrations**. When you click **Apply** on
a proposal, llm-curator POSTs the export JSON to your URL.

**Request headers:**
```
Content-Type:              application/json
X-LLM-Curator-Event:       proposal.applied
X-LLM-Curator-Timestamp:   1718000000
X-LLM-Curator-Signature:   sha256=<hmac>   # only if secret is configured
```

**Verifying the signature (Python):**
```python
import hashlib, hmac

def verify(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

### Applying Changes — Recipes by Stack

#### LiteLLM (`litellm_config.yaml`)

```python
import yaml, requests

export = requests.get("http://localhost:8088/api/proposals/3/export").json()

with open("litellm_config.yaml") as f:
    config = yaml.safe_load(f)

model_map = {m["model_name"]: m for m in config["model_list"]}

for change in export["changes"]:
    alias = change["alias"]
    if change["kind"] == "replace" and alias in model_map:
        # Update the litellm_params model string
        model_map[alias]["litellm_params"]["model"] = f"openrouter/{change['to_model']}"
    elif change["kind"] == "add":
        config["model_list"].append({
            "model_name": alias,
            "litellm_params": {"model": f"openrouter/{change['to_model']}"}
        })
    elif change["kind"] == "remove":
        config["model_list"] = [m for m in config["model_list"] if m["model_name"] != alias]

with open("litellm_config.yaml", "w") as f:
    yaml.dump(config, f)

# LiteLLM hot-reloads on file change — no restart needed
```

#### Direct OpenRouter calls (model string in env/config)

```python
import os, requests

export = requests.get("http://localhost:8088/api/proposals/3/export").json()

# Build a mapping of alias → model_id from the proposal
routing_updates = {}
for change in export["changes"]:
    if change["kind"] in ("replace", "add"):
        routing_updates[change["alias"]] = change["to_model"]
    elif change["kind"] == "remove":
        routing_updates[change["alias"]] = None

# Apply to your routing table however it's stored
print(routing_updates)
# {"reasoning": "openai/gpt-oss-120b:free", "coding": "qwen3-coder:480b-cloud"}
```

#### FastAPI / Express webhook handler

```python
from fastapi import FastAPI, Request, Header
import hmac, hashlib

app = FastAPI()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

@app.post("/webhooks/llm-curator")
async def handle_proposal(request: Request,
                           x_llm_curator_signature: str = Header(default="")):
    body = await request.body()

    # Verify signature
    if WEBHOOK_SECRET:
        expected = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_llm_curator_signature):
            return {"error": "invalid signature"}, 401

    payload = await request.json()
    if payload.get("event") == "proposal.applied":
        for change in payload.get("changes", []):
            apply_routing_change(change)   # your logic here

    return {"ok": True}
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/proposals/` | List proposals (latest first) |
| GET | `/api/proposals/{id}` | Full proposal detail |
| GET | `/api/proposals/{id}/export` | Clean JSON export for integration |
| POST | `/api/proposals/{id}/apply` | Mark applied + fire webhook |
| POST | `/api/proposals/{id}/reject` | Mark rejected |
| GET | `/api/leaderboard/` | Model scores by grader version |
| GET | `/api/registry/` | All known models with pricing |
| GET | `/api/settings/` | Provider config (keys masked) |
| PUT | `/api/settings/{key}` | Update a setting |
| POST | `/api/settings/test/{provider}` | Live connection test |

**Apply/Reject body:**
```json
{ "note": "optional reviewer comment" }
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key | — |
| `OLLAMA_URL` | Ollama base URL | `http://localhost:11434` |
| `WEBHOOK_URL` | Webhook delivery endpoint | — |
| `WEBHOOK_SECRET` | HMAC signing secret | — |
| `DATABASE_URL` | Postgres DSN | `postgresql://parcon:...@localhost:5434/curator` |

All variables can also be set via **Settings → Configure** in the UI; DB values
take precedence over env vars and take effect immediately without restarting.

---

## Design Principles

- **No LLM-as-judge** — all graders are deterministic Python
- **Read-only advisor** — proposals are never auto-applied; a human always approves
- **Provider-agnostic export** — the proposal JSON format is independent of your stack
- **Graceful degradation** — optional deps (sympy, rapidfuzz, pyyaml) degrade to simpler fallbacks

---

*llm-curator v0.4 · github.com/ksriram20/llm-curator*
