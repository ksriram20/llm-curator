"""LLM Curator — eval/proposal eligibility policy.

Single source of truth for "which models should the rotation actually test"
and "which models can the proposal generator suggest as candidates".

Why this exists:
  Without filtering, the picker happily evaluates every free model in the
  registry. That includes:
    - DeepSeek models routed via OpenRouter or Ollama Cloud (Sriram's policy:
      DeepSeek is accessed only via direct DeepSeek API, never these proxies)
    - 22+ OpenRouter free models that aren't in our litellm_config and have
      strict rate limits (50 req/day, 20 RPM — they 429 mid-eval and pollute
      the leaderboard with phantom errors)

Policy rules (set 2026-05-20 with Sriram):

  RULE 1 — DeepSeek exclusion
    Any model whose model_id begins with "deepseek/" or "deepseek-" on a
    non-direct-API source (i.e. openrouter or ollama-cloud) is INELIGIBLE,
    regardless of in_litellm status. DeepSeek runs only through the direct
    api.deepseek.com route, which isn't tracked in the registry today.

  RULE 2 — OpenRouter free auto-eval restriction
    OpenRouter free models (is_free=TRUE, source='openrouter') are INELIGIBLE
    for auto-rotation UNLESS:
      (a) the model is already in litellm_config (in_litellm=TRUE), OR
      (b) the model is on the OPENROUTER_FREE_OVERRIDES allow-list below.

  RULE 3 — Ollama Cloud verified-free non-DeepSeek
    Eligible. These are the primary eval pool.

  RULE 4 — In-litellm models (any source)
    Eligible. We use them in production; eval data on them matters most.

The override list is a deliberate, manually-curated bypass for OpenRouter free
models that community testing has shown to be reliable enough to evaluate.
Sources for current entries:
  - https://brainroad.com/openrouter-free-models-which-ones-actually-work-for-ai-agents/
  - https://www.teamday.ai/blog/best-free-ai-models-openrouter-2026

Manual override flag:
  Manual CLI invocations (`eval_runner --model X --source Y`) bypass policy.
  Policy filtering only applies to the rotation picker and the proposal
  candidate finder.
"""
from __future__ import annotations

# ── Allow-list overrides ─────────────────────────────────────────────────────

# OpenRouter free models that bypass Rule 2 (not in litellm but still eligible).
# Add entries with one-line rationale; keep this list short and audited.
#
# 2026-05-20 — Empty by design. Web research surfaced `qwen/qwen3-235b-a22b:free`
# as the top community-recommended reliable free OR model. Checking the live
# OpenRouter catalog (357 models in our registry) showed:
#   - qwen/qwen3-235b-a22b      → exists, but PAID only
#   - qwen/qwen3-235b-a22b-2507 → exists, but PAID only
#   - qwen3-235b free variant   → no longer exists
# This is exactly the volatility the community articles warned about.
# Two other free alternatives exist but are either redundant or off-mission:
#   - qwen/qwen3-next-80b-a3b-instruct:free → redundant with qwen3-next:80b-cloud
#   - qwen/qwen3-coder:free                 → code-specific, outside default eval scope
# Net: sufficient free models already in registry for eval surface. Leaving overrides
# empty until community evidence or eval data points at a specific winner that's
# actually free TODAY.
OPENROUTER_FREE_OVERRIDES: dict[str, str] = {
    # Add entries here as: "model_id": "one-line rationale (with date)"
}


# ── Rule helpers ─────────────────────────────────────────────────────────────

def _is_deepseek_proxy(model_id: str, source: str) -> bool:
    """Rule 1: DeepSeek through non-direct-API routes."""
    if source not in ("openrouter", "ollama-cloud"):
        return False
    m = (model_id or "").lower()
    # OpenRouter listing format: 'deepseek/deepseek-v4-flash[:free]'
    # Ollama Cloud format:       'deepseek-v3.2:cloud', 'deepseek-v4-flash:cloud', ...
    return m.startswith("deepseek/") or m.startswith("deepseek-") or m.startswith("deepseek:")


def is_eval_eligible(
    model_id: str,
    source: str,
    is_free: bool,
    in_litellm: bool,
) -> tuple[bool, str | None]:
    """Return (eligible, reason_if_not).

    Reason is None when eligible. Otherwise a short string explaining why
    the rotation picker / proposal candidate finder skipped this row.
    """
    # Rule 1: DeepSeek proxy routes — never eligible
    if _is_deepseek_proxy(model_id, source):
        return False, "policy: DeepSeek accessed via direct API only (not OpenRouter/Ollama)"

    # Rule 4: anything already in litellm_config is eligible (subject to Rule 1)
    if in_litellm:
        return True, None

    # Rule 2: OpenRouter free outside litellm — needs explicit override
    if source == "openrouter" and is_free:
        if model_id in OPENROUTER_FREE_OVERRIDES:
            return True, None
        return False, "policy: OpenRouter free model not in litellm_config and not on override list"

    # Rule 3 (and default): everything else (Ollama Cloud free, etc.) is eligible
    return True, None


# ── Convenience: explain why a registry row is or isn't eligible ─────────────

def explain(model_id: str, source: str, is_free: bool, in_litellm: bool) -> str:
    eligible, reason = is_eval_eligible(model_id, source, is_free, in_litellm)
    tag = "ELIGIBLE" if eligible else "SKIP"
    return f"{tag:<8} {model_id:<55} [{source}]  {reason or ''}"


if __name__ == "__main__":
    # Quick sanity print against known cases
    cases = [
        ("deepseek/deepseek-v4-flash",       "openrouter",   False, True),
        ("deepseek/deepseek-v4-flash:free",  "openrouter",   True,  True),
        ("deepseek-v4-flash:cloud",          "ollama-cloud", False, False),
        ("gpt-oss:120b-cloud",               "ollama-cloud", True,  True),
        ("cogito-2.1:671b-cloud",            "ollama-cloud", True,  True),
        ("openai/gpt-oss-120b:free",         "openrouter",   True,  True),
        ("nvidia/nemotron-3-super-120b-a12b:free", "openrouter", True, True),
        # Unaliased OR free model — should SKIP (no override active)
        ("qwen/qwen3-next-80b-a3b-instruct:free", "openrouter", True, False),
    ]
    for c in cases:
        print(explain(*c))
