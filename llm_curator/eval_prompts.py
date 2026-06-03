"""Fixed eval prompts + deterministic graders for llm-curator.

Design rules:
  - Every prompt is short (keep tokens tiny — single eval ≈ 1.5K tokens total)
  - Every grader is deterministic: exact match / substring / JSON schema / length
  - No LLM-as-judge in this phase (we don't want one model grading another)
  - Each prompt belongs to ONE use_case; multiple prompts per use_case are fine
  - Adding a new prompt: append to PROMPTS list. Schema below.

Schema per prompt:
  {
    "id":         unique short slug (also goes into llm_evals.eval_name)
    "use_case":   one of {reasoning, extraction, classification, summarization}
    "system":     optional system prompt (None to skip)
    "user":       user message text
    "expected":   reference answer (for graders that need it; can be None)
    "grader":     callable (output_text, expected) -> float in [0.0, 1.0]
  }
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

# ── Graders (deterministic) ─────────────────────────────────────────────────


def grade_exact(output: str, expected: str) -> float:
    """1.0 if normalised output equals normalised expected, else 0.0."""
    return 1.0 if output.strip().lower() == expected.strip().lower() else 0.0


def grade_contains(output: str, expected: str) -> float:
    """1.0 if expected appears as substring (case-insensitive)."""
    return 1.0 if expected.strip().lower() in output.strip().lower() else 0.0


def grade_integer(output: str, expected: str) -> float:
    """Pull the first integer from output; compare to expected integer."""
    m = re.search(r"-?\d+", output)
    if not m:
        return 0.0
    try:
        return 1.0 if int(m.group(0)) == int(expected) else 0.0
    except ValueError:
        return 0.0


def grade_json_keys(output: str, expected: str) -> float:
    """
    Expected is a JSON string with a target dict. Output should parse to a dict
    containing all the expected keys with matching values (case-insensitive
    string compare). Score = (matching_keys / total_expected_keys).
    """
    try:
        # Try to extract JSON from output even if wrapped in markdown code fences
        cleaned = output.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        out_obj = json.loads(cleaned)
        exp_obj = json.loads(expected)
    except (json.JSONDecodeError, ValueError):
        return 0.0
    if not isinstance(out_obj, dict) or not isinstance(exp_obj, dict):
        return 0.0
    total = len(exp_obj)
    if total == 0:
        return 1.0
    matched = 0
    for k, v in exp_obj.items():
        out_v = out_obj.get(k)
        if out_v is None:
            continue
        # Loose compare: stringify both, lowercase
        if str(out_v).strip().lower() == str(v).strip().lower():
            matched += 1
    return matched / total


def grade_length(_output: str, _expected: str) -> float:
    """Score by word count: 1.0 if 5-25 words; linearly decays outside."""
    n = len(_output.strip().split())
    if 5 <= n <= 25:
        return 1.0
    if n < 5:
        return max(0.0, n / 5.0)
    # n > 25: decay by 1/(n/25)
    return max(0.0, 25.0 / n)


# ── Eval prompt definitions ─────────────────────────────────────────────────

PROMPTS: list[dict[str, Any]] = [
    # ── reasoning ──────────────────────────────────────────────────────────
    {
        "id": "reasoning_widgets",
        "use_case": "reasoning",
        "system": None,
        "user": (
            "If 5 machines make 5 widgets in 5 minutes, how many minutes does it "
            "take 100 machines to make 100 widgets? Respond with only a single integer."
        ),
        "expected": "5",
        "grader": grade_integer,
    },
    {
        "id": "reasoning_age",
        "use_case": "reasoning",
        "system": None,
        "user": (
            "Anita is 3 times as old as her son Ravi. In 15 years, she will be twice "
            "as old as Ravi. What is Ravi's current age? Respond with only a single integer."
        ),
        "expected": "15",
        "grader": grade_integer,
    },

    # ── extraction ──────────────────────────────────────────────────────────
    {
        "id": "extraction_msme_json",
        "use_case": "extraction",
        "system": "Return ONLY valid JSON, no prose or markdown.",
        "user": (
            'Extract as JSON with keys company_name, year_founded, location, '
            'employee_count, revenue_inr_crore, cin.\n\n'
            '"ABC Manufacturing Ltd, established in 2015, is a Mumbai-based MSME '
            'with 47 employees and annual revenue of Rs 8.5 crore. '
            'CIN: U28910MH2015PTC123456."'
        ),
        "expected": json.dumps({
            "company_name": "ABC Manufacturing Ltd",
            "year_founded": 2015,
            "location": "Mumbai",
            "employee_count": 47,
            "revenue_inr_crore": 8.5,
            "cin": "U28910MH2015PTC123456",
        }),
        "grader": grade_json_keys,
    },

    # ── classification ─────────────────────────────────────────────────────
    {
        "id": "classification_doc_type",
        "use_case": "classification",
        "system": None,
        "user": (
            "Classify the following into one category from: "
            "[policy_change, market_news, research_paper, opinion_piece, advertisement]. "
            "Respond with ONLY the category name, nothing else.\n\n"
            "Text: 'RBI revises priority sector lending norms for MSMEs - "
            "increases sub-target for micro enterprises from 7.5% to 8.5%.'"
        ),
        "expected": "policy_change",
        "grader": grade_exact,
    },
    {
        "id": "classification_sentiment",
        "use_case": "classification",
        "system": None,
        "user": (
            "Classify the following review's sentiment as one of: positive, negative, neutral. "
            "Respond with ONLY the single word.\n\n"
            "'The service was disappointing and the wait time was unreasonable.'"
        ),
        "expected": "negative",
        "grader": grade_exact,
    },

    # ── summarization ──────────────────────────────────────────────────────
    {
        "id": "summarization_one_sentence",
        "use_case": "summarization",
        "system": None,
        "user": (
            "Summarize the following in exactly ONE sentence under 25 words.\n\n"
            "'India's MSME sector contributes about 30% to the country's GDP and "
            "employs over 110 million people. Despite its scale, MSMEs face persistent "
            "challenges accessing formal credit, with informal sources still funding "
            "the majority of small enterprises. The TReDS platform was launched in "
            "2017 to address invoice-financing gaps for the sector.'"
        ),
        "expected": "",  # not used by length grader
        "grader": grade_length,
    },
]


# Public helpers ────────────────────────────────────────────────────────────


def use_cases() -> list[str]:
    """Distinct use-case labels across all prompts."""
    return sorted({p["use_case"] for p in PROMPTS})


def total_input_tokens_estimate() -> int:
    """Rough token estimate (chars/4) for budgeting."""
    return sum(
        (len(p.get("system") or "") + len(p["user"])) // 4
        for p in PROMPTS
    )


if __name__ == "__main__":
    # Quick sanity print
    print(f"Total prompts: {len(PROMPTS)}")
    print(f"Use cases: {use_cases()}")
    print(f"Est. input tokens per full eval: ~{total_input_tokens_estimate()}")
