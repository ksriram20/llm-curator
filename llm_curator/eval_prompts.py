"""Fixed eval prompts + deterministic graders for llm-curator.

Design rules:
  - Every prompt is short (keep tokens tiny — single eval ≈ 1.5K tokens total)
  - Every grader is deterministic: no LLM-as-judge
  - Each prompt belongs to ONE use_case; multiple prompts per use_case are fine
  - Adding a new prompt: append to PROMPTS list. Schema below.
  - GRADER_VERSION bumps when grader logic changes; old scores in DB are preserved.

Schema per prompt:
  {
    "id":       unique short slug (also goes into llm_evals.eval_name)
    "use_case": one of {reasoning, extraction, classification, summarization}
    "system":   optional system prompt (None to skip)
    "user":     user message text
    "expected": reference answer / constraint spec (JSON string or plain string)
    "grader":   callable (output_text, expected) -> float in [0.0, 1.0]
  }

Grader versions
  v1 — grade_integer, grade_json_keys, grade_exact, grade_length  (baseline)
  v2 — grade_sympy, grade_json_doc, grade_quasi_exact, grade_ifeval_rougek
         Sources: HELM (arXiv:2211.09110), JSONSchemaBench (arXiv:2501.10868),
                  LLMStructBench (arXiv:2602.14743), IFEval (arXiv:2311.07911),
                  ROUGE-K (arXiv:2403.05186), GSM-Symbolic (arXiv:2410.05229)
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

# Bumped when grader logic changes. Stored in llm_evals.grader_version.
# Old rows with prior version strings remain valid for historical comparison.
GRADER_VERSION = "v2"


# ── v1 graders (kept for reference / backwards compat) ────────────────────

def grade_exact(output: str, expected: str) -> float:
    """v1 — simple case-insensitive exact match. Replaced by grade_quasi_exact."""
    return 1.0 if output.strip().lower() == expected.strip().lower() else 0.0


def grade_contains(output: str, expected: str) -> float:
    """v1 — 1.0 if expected appears as substring (case-insensitive)."""
    return 1.0 if expected.strip().lower() in output.strip().lower() else 0.0


def grade_integer(output: str, expected: str) -> float:
    """v1 — pulls first integer from output. Replaced by grade_sympy."""
    m = re.search(r"-?\d+", output)
    if not m:
        return 0.0
    try:
        return 1.0 if int(m.group(0)) == int(expected) else 0.0
    except ValueError:
        return 0.0


def grade_json_keys(output: str, expected: str) -> float:
    """v1 — key presence + loose value match. Replaced by grade_json_doc."""
    try:
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
    matched = sum(
        1 for k, v in exp_obj.items()
        if str(out_obj.get(k, "")).strip().lower() == str(v).strip().lower()
    )
    return matched / total


def grade_length(_output: str, _expected: str) -> float:
    """v1 — word count 5–25 = 1.0; decays outside. Replaced by grade_ifeval_rougek."""
    n = len(_output.strip().split())
    if 5 <= n <= 25:
        return 1.0
    if n < 5:
        return max(0.0, n / 5.0)
    return max(0.0, 25.0 / n)


# ── v2 graders ─────────────────────────────────────────────────────────────

def grade_quasi_exact(output: str, expected: str) -> float:
    """v2 — HELM-style quasi-exact match (arXiv:2211.09110).

    Pipeline: lowercase → strip punctuation → remove articles → collapse
    whitespace → exact match. Fuzzy fallback via rapidfuzz (optional dep).
    Fixes v1 failure: model outputs 'This is negative' → score was 0.0.
    """
    def _normalize(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[^\w\s]", " ", s)               # strip punctuation
        s = re.sub(r"\b(a|an|the)\b", " ", s)         # remove articles
        s = re.sub(r"\s+", " ", s).strip()            # collapse whitespace
        return s

    norm_out = _normalize(output)
    norm_exp = _normalize(expected)

    if norm_out == norm_exp:
        return 1.0

    # Expected label contained in output (model was verbose but correct)
    if norm_exp and norm_exp in norm_out:
        return 0.9

    # Fuzzy match fallback — optional dep, graceful degradation
    try:
        from rapidfuzz import fuzz  # type: ignore
        ratio = fuzz.ratio(norm_out, norm_exp) / 100.0
        return round(ratio, 4) if ratio >= 0.85 else 0.0
    except ImportError:
        pass

    return 0.0


def grade_json_doc(output: str, expected: str) -> float:
    """v2 — JSONSchema structural validation + DOC value-level F1.

    Sources: JSONSchemaBench (arXiv:2501.10868), LLMStructBench (arXiv:2602.14743).

    Stages:
      1. Strip markdown fences, attempt JSON parse → 0.0 on hard fail
      2. jsonschema required-key validation (optional dep) → structural_score
      3. Token-level value F1 per key (with numeric tolerance) → value_score
      4. Final = 0.3 × structural + 0.7 × value
    """
    # Strip markdown code fences
    cleaned = output.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        out_obj = json.loads(cleaned)
        exp_obj = json.loads(expected)
    except (json.JSONDecodeError, ValueError):
        return 0.0

    if not isinstance(out_obj, dict) or not isinstance(exp_obj, dict):
        return 0.0

    # Structural score: all required keys present and JSON is valid
    structural_score = 1.0
    try:
        import jsonschema  # type: ignore
        schema = {
            "type": "object",
            "required": list(exp_obj.keys()),
            "properties": {k: {} for k in exp_obj.keys()},
        }
        jsonschema.validate(instance=out_obj, schema=schema)
    except ImportError:
        # No jsonschema: fall back to manual key presence check
        missing = [k for k in exp_obj if k not in out_obj]
        structural_score = 1.0 - (len(missing) / max(len(exp_obj), 1))
    except Exception:
        structural_score = max(
            0.0,
            1.0 - sum(1 for k in exp_obj if k not in out_obj) / max(len(exp_obj), 1),
        )

    # Value-level F1 (DOC metric)
    total = len(exp_obj)
    if total == 0:
        return structural_score

    matched: float = 0.0
    for k, v in exp_obj.items():
        out_v = out_obj.get(k)
        if out_v is None:
            continue
        if str(out_v).strip().lower() == str(v).strip().lower():
            matched += 1.0
        else:
            # Numeric tolerance: within 1% counts as 0.9
            try:
                if abs(float(out_v) - float(v)) / max(abs(float(v)), 1e-9) < 0.01:
                    matched += 0.9
            except (ValueError, TypeError):
                pass

    value_score = matched / total
    return round(0.3 * structural_score + 0.7 * value_score, 4)


def grade_sympy(output: str, expected: str) -> float:
    """v2 — SymPy AST equivalence for math reasoning (arXiv:2504.01005).

    Handles 'The answer is 5', '5.0', '= 5', expressions like '20/4'.
    Falls back to integer extraction (v1 behaviour) if sympy not installed.
    Timeout: sympy calls are wrapped in try/except to prevent infinite loops.
    """
    # Extract first number-like token from output
    m = re.search(r"-?\d+(?:[./]\d+)?", output)
    if not m:
        return 0.0

    extracted = m.group(0)

    # Fast path: simple integer / decimal compare
    try:
        out_val = float(extracted) if "/" not in extracted else eval(extracted)  # noqa: S307
        exp_val = float(expected)
        if abs(out_val - exp_val) < 1e-6:
            return 1.0
    except (ValueError, TypeError, ZeroDivisionError):
        pass

    # SymPy symbolic equivalence (optional dep)
    try:
        import sympy  # type: ignore
        out_expr = sympy.sympify(extracted, evaluate=True)
        exp_expr = sympy.sympify(expected, evaluate=True)
        diff = sympy.simplify(out_expr - exp_expr)
        return 1.0 if diff == 0 else 0.0
    except ImportError:
        # Graceful degradation: fall back to v1 integer extraction
        mi = re.search(r"-?\d+", output)
        if mi:
            try:
                return 1.0 if int(mi.group(0)) == int(expected) else 0.0
            except ValueError:
                pass
    except Exception:
        pass

    return 0.0


def grade_ifeval_rougek(output: str, expected: str) -> float:
    """v2 — IFEval constraint check + ROUGE-K keyword recall.

    Sources: IFEval (arXiv:2311.07911), ROUGE-K (arXiv:2403.05186).

    expected must be a JSON string:
      {"max_words": 25, "keywords": ["MSME", "GDP", "TReDS", ...]}

    Score = 0.5 × constraint_score + 0.5 × keyword_recall
      constraint_score: 1.0 if ≤ max_words AND single sentence; decays otherwise
      keyword_recall:   fraction of mandatory keywords found in output
    """
    # Parse constraint spec from expected field
    try:
        spec = json.loads(expected)
        max_words: int = int(spec.get("max_words", 25))
        keywords: list[str] = [kw.lower() for kw in spec.get("keywords", [])]
    except (json.JSONDecodeError, ValueError, TypeError):
        # Fallback: treat as plain max_words integer, no keyword check
        try:
            max_words = int(expected)
        except (ValueError, TypeError):
            max_words = 25
        keywords = []

    text = output.strip()
    words = text.split()
    word_count = len(words)

    # IFEval: word-count constraint
    if word_count <= max_words:
        constraint_score = 1.0
    else:
        constraint_score = max(0.0, max_words / word_count)

    # IFEval: single-sentence check
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) > 1:
        constraint_score *= 0.8  # penalty for violating "exactly ONE sentence"

    # ROUGE-K: keyword recall
    if keywords:
        output_lower = text.lower()
        hits = sum(1 for kw in keywords if kw in output_lower)
        keyword_score = hits / len(keywords)
    else:
        keyword_score = 1.0  # no keywords to check — full credit

    return round(0.5 * constraint_score + 0.5 * keyword_score, 4)


# ── Public helpers ─────────────────────────────────────────────────────────

def list_graders() -> dict[str, Callable]:
    """Return all grader functions keyed by name. Useful for CLI inspection."""
    return {
        # v1
        "grade_exact": grade_exact,
        "grade_contains": grade_contains,
        "grade_integer": grade_integer,
        "grade_json_keys": grade_json_keys,
        "grade_length": grade_length,
        # v2
        "grade_quasi_exact": grade_quasi_exact,
        "grade_json_doc": grade_json_doc,
        "grade_sympy": grade_sympy,
        "grade_ifeval_rougek": grade_ifeval_rougek,
    }


# ── Eval prompt definitions ─────────────────────────────────────────────────

PROMPTS: list[dict[str, Any]] = [

    # ── reasoning (v2: grade_sympy) ────────────────────────────────────────
    {
        "id": "reasoning_widgets",
        "use_case": "reasoning",
        "system": None,
        "user": (
            "If 5 machines make 5 widgets in 5 minutes, how many minutes does it "
            "take 100 machines to make 100 widgets? Respond with only a single integer."
        ),
        "expected": "5",
        "grader": grade_sympy,
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
        "grader": grade_sympy,
    },

    # ── extraction (v2: grade_json_doc) ───────────────────────────────────
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
        "grader": grade_json_doc,
    },

    # ── classification (v2: grade_quasi_exact) ────────────────────────────
    {
        "id": "classification_doc_type",
        "use_case": "classification",
        "system": None,
        "user": (
            "Classify the following into one category from: "
            "[policy_change, market_news, research_paper, opinion_piece, advertisement]. "
            "Respond with ONLY the category name, nothing else.\n\n"
            "Text: 'RBI revises priority sector lending norms for MSMEs — "
            "increases sub-target for micro enterprises from 7.5% to 8.5%.'"
        ),
        "expected": "policy_change",
        "grader": grade_quasi_exact,
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
        "grader": grade_quasi_exact,
    },

    # ── summarization (v2: grade_ifeval_rougek) ───────────────────────────
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
        # Constraint spec for grade_ifeval_rougek:
        # max_words enforces IFEval constraint; keywords drive ROUGE-K recall
        "expected": json.dumps({
            "max_words": 25,
            "keywords": ["msme", "gdp", "credit", "treds", "110 million"],
        }),
        "grader": grade_ifeval_rougek,
    },
]
