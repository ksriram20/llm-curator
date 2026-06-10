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
    "use_case": one of {reasoning, extraction, classification, summarization, tool_use, structured_data, code_exec}
    "system":   optional system prompt (None to skip; not used for tool_use)
    "user":     user message text
    "expected": reference answer / constraint spec (JSON string or plain string)
    "grader":   callable (output_text, expected) -> float in [0.0, 1.0]
    "tools":    list of tool schemas (REQUIRED for tool_use; omit for all others)
  }

  tool_use prompts are sent via call_with_tools() in eval_runner, not call().
  The grader receives output as JSON: {"name": "fn_name", "arguments": {...}}.

Grader versions
  v1 — grade_integer, grade_json_keys, grade_exact, grade_length  (baseline)
  v2 — grade_sympy, grade_json_doc, grade_quasi_exact, grade_ifeval_rougek
         Sources: HELM (arXiv:2211.09110), JSONSchemaBench (arXiv:2501.10868),
                  LLMStructBench (arXiv:2602.14743), IFEval (arXiv:2311.07911),
                  ROUGE-K (arXiv:2403.05186), GSM-Symbolic (arXiv:2410.05229)
  v3 — adds grade_tool_call
         Source: BFCL (arXiv:2504.00914) — AST/JSON comparison for function calling
  v4 — adds grade_struct_data, grade_code_exec
         Sources: StructEval (TMLR 2025) — YAML/XML/CSV syntax + dot-path validation
                  CRUXEval (arXiv:2401.03065) — subprocess execution, score = passed/total
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

# Bumped when grader logic changes. Stored in llm_evals.grader_version.
# Old rows with prior version strings remain valid for historical comparison.
GRADER_VERSION = "v4"


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


# ── v3 graders ─────────────────────────────────────────────────────────────

def grade_tool_call(output: str, expected: str) -> float:
    """v3 — BFCL-inspired deterministic tool-call grader (arXiv:2504.00914).

    output:   JSON string from call_with_tools() — {"name": "...", "arguments": {...}}
              Empty string when the model produced no tool call.
    expected: JSON string — {"function": "name", "arguments": {...}, "required_exact": [...]}
              required_exact: keys whose values must match exactly (case-insensitive string).

    Score components:
      0.40 — function name match (binary)
      0.20 — required argument keys present (fraction of exp_args keys found)
      0.20 — argument type correctness (fraction matching declared Python type)
      0.20 — exact value match for required_exact keys (fraction)
    """
    try:
        exp = json.loads(expected)
        exp_fn: str = exp.get("function", "")
        exp_args: dict = exp.get("arguments", {})
        required_exact: list = exp.get("required_exact", [])
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0.0

    if not output:
        return 0.0

    try:
        got = json.loads(output)
        got_fn: str = got.get("name", "")
        raw_args = got.get("arguments", {})
        got_args: dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0.0

    # Component 1: function name (0.40)
    fn_score = 1.0 if got_fn == exp_fn else 0.0

    # Component 2: required args present (0.20)
    if exp_args:
        args_present_score = sum(1 for k in exp_args if k in got_args) / len(exp_args)
    else:
        args_present_score = 1.0

    # Component 3: type correctness (0.20)
    # bool must be checked before int (bool is subclass of int in Python)
    if exp_args:
        type_hits = 0
        for k, v in exp_args.items():
            got_v = got_args.get(k)
            if got_v is None:
                continue
            if isinstance(v, bool):
                type_hits += 1 if isinstance(got_v, bool) else 0
            elif isinstance(v, (int, float)):
                type_hits += 1 if isinstance(got_v, (int, float)) and not isinstance(got_v, bool) else 0
            elif type(v) is type(got_v):
                type_hits += 1
        type_score = type_hits / len(exp_args)
    else:
        type_score = 1.0

    # Component 4: exact value match for required_exact keys (0.20)
    if required_exact:
        exact_hits = sum(
            1 for k in required_exact
            if str(got_args.get(k, "")).strip().lower() == str(exp_args.get(k, "")).strip().lower()
        )
        value_score = exact_hits / len(required_exact)
    else:
        value_score = 1.0

    return round(0.40 * fn_score + 0.20 * args_present_score + 0.20 * type_score + 0.20 * value_score, 4)


# ── v4 graders ─────────────────────────────────────────────────────────────

def grade_struct_data(output: str, expected: str) -> float:
    """v4 — StructEval-inspired structured-output grader (TMLR 2025).

    Two stages:
      syntax_ok      (0.40) — parse succeeds for the declared format
      key_validation (0.60) — dot-path keys present + required_values match

    expected: JSON string — {
        "format": "yaml" | "xml" | "csv",
        "required_keys":   ["services.web"],     # dot-path presence check
        "required_values": {"name": "myapp"}     # leaf exact-match check
    }
    """
    import csv as _csv
    import io

    try:
        spec = json.loads(expected)
        fmt: str = spec.get("format", "yaml").lower()
        req_keys: list = spec.get("required_keys", [])
        req_vals: dict = spec.get("required_values", {})
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0.0

    # Strip markdown fences
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Stage 1: syntax
    syntax_ok = 0.0
    obj: Any = None
    try:
        if fmt == "yaml":
            import yaml  # type: ignore
            obj = yaml.safe_load(text)
            syntax_ok = 1.0 if isinstance(obj, dict) else 0.0
        elif fmt == "xml":
            import xml.etree.ElementTree as ET
            obj = ET.fromstring(text)
            syntax_ok = 1.0
        elif fmt == "csv":
            rdr = _csv.DictReader(io.StringIO(text))
            rows = list(rdr)
            obj = {"fields": list(rdr.fieldnames or []), "rows": rows}
            syntax_ok = 1.0 if obj["fields"] else 0.0
    except Exception:
        pass

    if syntax_ok == 0.0 or obj is None:
        return 0.0

    # Stage 2: dot-path helpers
    def _yaml_get(node: Any, path: str) -> Any:
        for part in path.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    def _xml_get(root: Any, path: str) -> Any:
        import xml.etree.ElementTree as ET  # noqa: F811
        cur = root
        for part in path.split(".")[1:]:  # skip root tag name
            cur = cur.find(part) if cur is not None else None
        return cur.text if cur is not None else None

    total = len(req_keys) + len(req_vals)
    if total == 0:
        return round(0.40 * syntax_ok + 0.60, 4)

    hits = 0.0
    if fmt == "yaml":
        hits += sum(1 for k in req_keys if _yaml_get(obj, k) is not None)
        hits += sum(
            1 for k, v in req_vals.items()
            if str(_yaml_get(obj, k) or "").strip().lower() == str(v).strip().lower()
        )
    elif fmt == "xml":
        hits += sum(1 for k in req_keys if _xml_get(obj, k) is not None)
        hits += sum(
            1 for k, v in req_vals.items()
            if str(_xml_get(obj, k) or "").strip().lower() == str(v).strip().lower()
        )
    elif fmt == "csv":
        fields = obj["fields"]
        rows = obj["rows"]
        hits += sum(1 for k in req_keys if k in fields)
        hits += sum(
            1 for k, v in req_vals.items()
            if any(str(r.get(k, "")).strip().lower() == str(v).strip().lower() for r in rows)
        )

    return round(0.40 * syntax_ok + 0.60 * (hits / total), 4)


def grade_code_exec(output: str, expected: str) -> float:
    """v4 — CRUXEval-inspired code-execution grader (arXiv:2401.03065).

    Extracts the named function from LLM output, builds a test harness,
    and runs it via subprocess with a 5-second timeout.
    Score = passed_cases / total_cases.

    expected: JSON string — {
        "fn_name": "solve",
        "cases": [{"args": [5], "expected": 55}]
    }

    Runs inside the curator container — no extra infrastructure needed.
    Prompts are hand-crafted so arbitrary-code risk is accepted by design.
    """
    import subprocess
    import sys as _sys

    try:
        spec = json.loads(expected)
        fn_name: str = spec.get("fn_name", "")
        cases: list = spec.get("cases", [])
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0.0

    if not fn_name or not cases:
        return 0.0

    # Strip markdown fences
    code = output.strip()
    if code.startswith("```"):
        code = re.sub(r"^```[a-z]*\n?", "", code)
        code = re.sub(r"\n?```\s*$", "", code)
    code = code.strip()

    if not code:
        return 0.0

    # Build test harness
    lines = [code, "", "_p = 0", "_t = 0"]
    for case in cases:
        args_r = ", ".join(repr(a) for a in case.get("args", []))
        exp_r = repr(case.get("expected"))
        lines += [
            "_t += 1",
            "try:",
            f"    _r = {fn_name}({args_r})",
            f"    if _r == {exp_r}: _p += 1",
            "except Exception: pass",
        ]
    lines.append("print(f'{_p}/{_t}')")

    try:
        result = subprocess.run(
            [_sys.executable, "-c", "\n".join(lines)],
            capture_output=True, text=True, timeout=5,
        )
        out = result.stdout.strip()
        if "/" in out:
            p, t = out.split("/", 1)
            return round(int(p) / max(int(t), 1), 4)
    except Exception:
        pass

    return 0.0


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
        # v3
        "grade_tool_call": grade_tool_call,
        # v4
        "grade_struct_data": grade_struct_data,
        "grade_code_exec": grade_code_exec,
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

    # ── tool_use (v3: grade_tool_call) ────────────────────────────────────
    # Canary prompts inspired by BFCL failure-mode analysis (arXiv:2504.00914).
    # Sent via call_with_tools(); grader receives JSON {"name":..., "arguments":...}.
    # eval_runner skips tool_use prompts for non-OpenRouter sources.

    {
        # Baseline: single tool, all argument values stated explicitly.
        "id": "tool_simple",
        "use_case": "tool_use",
        "system": None,
        "user": "What is the current weather in Paris in celsius?",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                            "unit": {
                                "type": "string",
                                "enum": ["celsius", "fahrenheit"],
                                "description": "Temperature unit",
                            },
                        },
                        "required": ["city", "unit"],
                    },
                },
            }
        ],
        "expected": json.dumps({
            "function": "get_weather",
            "arguments": {"city": "Paris", "unit": "celsius"},
            "required_exact": ["unit"],
        }),
        "grader": grade_tool_call,
    },
    {
        # Distractor: three semantically similar tools — model must read descriptions.
        "id": "tool_distractor",
        "use_case": "tool_use",
        "system": None,
        "user": "A customer named Alice wants to look up her past orders.",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search_products",
                    "description": "Search the product catalog by keyword.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_orders",
                    "description": "Look up past orders placed by a customer.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_name": {"type": "string"},
                        },
                        "required": ["customer_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_inventory",
                    "description": "Check current inventory levels for a product SKU.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                        },
                        "required": ["sku"],
                    },
                },
            },
        ],
        "expected": json.dumps({
            "function": "search_orders",
            "arguments": {"customer_name": "Alice"},
            "required_exact": ["customer_name"],
        }),
        "grader": grade_tool_call,
    },
    {
        # Abstain: no tool fits — model should call no_action, not fabricate a call.
        "id": "tool_abstain",
        "use_case": "tool_use",
        "system": None,
        "user": "What is your refund policy for electronics purchased online?",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "book_flight",
                    "description": "Book a flight between two cities.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "origin": {"type": "string"},
                            "destination": {"type": "string"},
                        },
                        "required": ["origin", "destination"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_flight_status",
                    "description": "Check the status of an existing flight booking.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "booking_id": {"type": "string"},
                        },
                        "required": ["booking_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "no_action",
                    "description": (
                        "Use this when no other tool is relevant to the user's question."
                    ),
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
        ],
        "expected": json.dumps({
            "function": "no_action",
            "arguments": {},
            "required_exact": [],
        }),
        "grader": grade_tool_call,
    },
    {
        # Schema-strict: argument values must satisfy enum + date format constraints.
        "id": "tool_schema_strict",
        "use_case": "tool_use",
        "system": None,
        "user": "Schedule a high priority appointment for the 15th of March 2025.",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "create_appointment",
                    "description": "Create a calendar appointment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {
                                "type": "string",
                                "description": "Appointment date in YYYY-MM-DD format.",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                                "description": "Appointment priority level.",
                            },
                        },
                        "required": ["date", "priority"],
                    },
                },
            }
        ],
        "expected": json.dumps({
            "function": "create_appointment",
            "arguments": {"date": "2025-03-15", "priority": "high"},
            "required_exact": ["date", "priority"],
        }),
        "grader": grade_tool_call,
    },
    {
        # Implicit args: values must be reasoned from context, not copied verbatim.
        "id": "tool_implicit_args",
        "use_case": "tool_use",
        "system": None,
        "user": "Move ₹5000 from savings account SA-001 to current account CA-002.",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "transfer_funds",
                    "description": "Transfer money between two accounts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "from_account": {"type": "string"},
                            "to_account": {"type": "string"},
                            "amount": {"type": "number"},
                            "currency": {
                                "type": "string",
                                "enum": ["USD", "EUR", "GBP", "INR"],
                            },
                        },
                        "required": ["from_account", "to_account", "amount", "currency"],
                    },
                },
            }
        ],
        "expected": json.dumps({
            "function": "transfer_funds",
            "arguments": {
                "from_account": "SA-001",
                "to_account": "CA-002",
                "amount": 5000,
                "currency": "INR",
            },
            "required_exact": ["from_account", "to_account", "currency"],
        }),
        "grader": grade_tool_call,
    },

    # ── structured_data (v4: grade_struct_data) ───────────────────────────
    # StructEval-inspired (TMLR 2025): syntax check + dot-path key validation.

    {
        "id": "struct_yaml_config",
        "use_case": "structured_data",
        "system": "Output ONLY the requested format. No prose, no markdown fences.",
        "user": (
            "Write a YAML configuration for a web application with:\n"
            "- name: myapp\n"
            "- version: 1.0\n"
            "- a services section with a 'web' service using image 'nginx' on port 80"
        ),
        "expected": json.dumps({
            "format": "yaml",
            "required_keys": ["services.web"],
            "required_values": {"name": "myapp", "services.web.image": "nginx"},
        }),
        "grader": grade_struct_data,
    },
    {
        "id": "struct_xml_record",
        "use_case": "structured_data",
        "system": "Output ONLY the requested format. No prose, no markdown fences.",
        "user": (
            "Write an XML record for an employee:\n"
            "- name: Jane Smith\n"
            "- department: Engineering\n"
            "- salary: 90000\n"
            "Use <employee> as the root element with child elements for each field."
        ),
        "expected": json.dumps({
            "format": "xml",
            "required_keys": ["employee.department", "employee.salary"],
            "required_values": {"employee.name": "Jane Smith"},
        }),
        "grader": grade_struct_data,
    },
    {
        "id": "struct_csv_report",
        "use_case": "structured_data",
        "system": "Output ONLY the requested format. No prose, no markdown fences.",
        "user": (
            "Write a 3-row CSV table of LLM benchmark results with columns: "
            "model, score, latency_ms. Use realistic model names and values."
        ),
        "expected": json.dumps({
            "format": "csv",
            "required_keys": ["model", "score", "latency_ms"],
            "required_values": {},
        }),
        "grader": grade_struct_data,
    },

    # ── code_exec (v4: grade_code_exec) ──────────────────────────────────
    # CRUXEval-inspired (arXiv:2401.03065): subprocess execution, score = passed/total.

    {
        "id": "code_list_filter",
        "use_case": "code_exec",
        "system": "Return ONLY valid Python code with no prose or markdown fences.",
        "user": (
            "Write a Python function named 'solve' that takes a list of numbers and a "
            "threshold value, and returns a new list of only the numbers strictly "
            "greater than the threshold, in their original order."
        ),
        "expected": json.dumps({
            "fn_name": "solve",
            "cases": [
                {"args": [[1, 5, 3, 8, 2], 4], "expected": [5, 8]},
                {"args": [[], 0], "expected": []},
                {"args": [[10, 20, 30], 25], "expected": [30]},
            ],
        }),
        "grader": grade_code_exec,
    },
    {
        "id": "code_string_reverse",
        "use_case": "code_exec",
        "system": "Return ONLY valid Python code with no prose or markdown fences.",
        "user": (
            "Write a Python function named 'solve' that takes a string of "
            "space-separated words and returns the words in reversed order joined by spaces."
        ),
        "expected": json.dumps({
            "fn_name": "solve",
            "cases": [
                {"args": ["hello world"], "expected": "world hello"},
                {"args": ["one two three four"], "expected": "four three two one"},
                {"args": ["single"], "expected": "single"},
            ],
        }),
        "grader": grade_code_exec,
    },
    {
        "id": "code_fizzbuzz",
        "use_case": "code_exec",
        "system": "Return ONLY valid Python code with no prose or markdown fences.",
        "user": (
            "Write a Python function named 'solve' that takes an integer n and returns "
            "a list of strings for numbers 1 to n: 'Fizz' if divisible by 3, 'Buzz' if "
            "divisible by 5, 'FizzBuzz' if divisible by both, otherwise the number as a string."
        ),
        "expected": json.dumps({
            "fn_name": "solve",
            "cases": [
                {"args": [5], "expected": ["1", "2", "Fizz", "4", "Buzz"]},
                {"args": [15], "expected": [
                    "1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8", "Fizz",
                    "Buzz", "11", "Fizz", "13", "14", "FizzBuzz",
                ]},
            ],
        }),
        "grader": grade_code_exec,
    },
]
