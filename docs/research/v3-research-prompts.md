# v0.3 Research Prompts

Prompts for commissioning external deep research into grading methods
beyond the v2 suite (Reasoning, Extraction, Classification, Summarization).
v0.3 scope adds Tool Use and explores further use cases.

Run both prompts independently. Gemini goes deep on academic literature;
Perplexity goes wide on real-world adoption and implementations.
Synthesise both outputs before designing v3 graders.

---

## Prompt 1 — Google Gemini Deep Research

> **How to use:** Open Gemini Advanced → Deep Research mode. Paste the prompt
> below as-is. Let it run its full research plan (typically 20–40 sources).
> Export the report and save as `docs/research/grading-methods-gemini-v3.md`.

---

I am building a deterministic LLM evaluation platform called llm-curator. It evaluates language models daily using programmatic, code-executable graders — no LLM-as-judge at any stage. The platform currently grades models across four use cases using v2 graders (published June 2026):

- **Reasoning** — SymPy AST equivalence (arXiv:2504.01005)
- **Extraction** — JSONSchema validation + DOC metric (arXiv:2501.10868, arXiv:2602.14743)
- **Classification** — HELM Quasi-Exact Match (arXiv:2211.09110)
- **Summarization** — IFEval constraint check + ROUGE-K keyword recall (arXiv:2311.07911, arXiv:2403.05186)

I am now designing **v0.3** of the grading suite. I need you to research the following, focusing on papers and benchmarks published or updated between **January 2024 and June 2026**:

**Research Question 1 — Tool Use / Function Calling**
What are the best deterministic methods for evaluating whether an LLM correctly executes a tool call or function call? Specifically:
- Does the model return parseable JSON matching a function schema?
- Does it select the correct function from a list of candidates?
- Does it populate arguments with correct types and values?
- Are there established benchmarks (ToolBench, APIBench, BFCL, ToolEval, or newer) that define grading rubrics I can adapt?
- What is the recommended scoring formula (binary pass/fail vs partial credit)?

**Research Question 2 — Beyond the current four: what use cases are missing?**
Given a production LLM routing system evaluating models for tasks like document analysis, data extraction, summarisation, classification, and tool calling — what additional use cases have rigorous deterministic evaluation methods available as of June 2026? Consider:
- Multi-step instruction following (beyond single-constraint IFEval)
- Factual grounding / citation accuracy (deterministic, not neural)
- Code generation (execution-based grading)
- Structured data generation (beyond JSON — CSV, YAML, XML)
- Language/dialect robustness (does the model degrade on non-English inputs?)

**Research Question 3 — Prompt contamination and eval robustness**
Our prompts are fixed and run daily. How do we detect or mitigate training data contamination (models memorising eval answers rather than reasoning)? What are the latest techniques for generating programmatically varied prompt instances (like GSM-Symbolic, arXiv:2410.05229) that can be applied to non-math use cases?

**Output format I need:**
For each grading method found, provide a structured entry with:
1. Use case category
2. Paper title + arXiv ID (or benchmark name + URL)
3. Core grading mechanism in plain terms (2–3 sentences)
4. Scoring range (binary / 0–1 continuous / custom)
5. Required Python dependencies
6. Implementation complexity: Easy / Medium / Hard
7. Adoption level: Research-only / Used in major benchmarks / Production-deployed
8. Why it is better than or complementary to the v2 grader for that use case

End with a **recommended priority order** for implementation, considering: grader quality, implementation effort, and value for a routing decision system.

Do not include methods that require a neural judge, human annotation, or are not reproducibly executable in a Python script.

---

## Prompt 2 — Perplexity Pro Deep Research

> **How to use:** Open Perplexity Pro → select "Deep Research" mode. Paste the
> prompt below. When the report is ready, export and save as
> `docs/research/grading-methods-perplexity-v3.md`.

---

I'm building a self-hosted LLM evaluation platform that scores language models using only deterministic, code-executable graders — no LLM-as-judge. The platform runs daily evals on models from OpenRouter and Ollama Cloud. I need to research what the AI engineering community is actually using in 2025–2026 for evaluating LLMs, beyond the standard academic benchmarks.

**Focus areas — find real-world, practitioner-level answers:**

**1. Tool Use / Function Calling evaluation (most urgent)**
- What benchmarks or grading frameworks are practitioners using right now to evaluate tool-calling quality in LLMs? Include: BFCL (Berkeley Function Calling Leaderboard), ToolBench, Gorilla APIBench, or any newer ones from 2025–2026.
- What does the actual scoring code look like? Are there open-source implementations I can adapt?
- Which providers (OpenRouter, Mistral, Anthropic, Google) publish tool-calling eval results and what metrics do they use?
- What is the minimal prompt + grader combo that reliably separates good from bad tool-calling models?

**2. What eval use cases are production teams adding in 2025–2026?**
- Search GitHub, Hugging Face, and engineering blogs for: what new eval categories are teams adding beyond reasoning/extraction/classification/summarisation?
- Look for: promptfoo configs, Inspect AI scorers, LangSmith eval templates, Braintrust datasets — what use cases appear most in production eval suites?
- Are there open-source eval suites released in 2025–2026 with deterministic graders worth borrowing from?

**3. Prompt rotation and anti-contamination techniques**
- What are engineering teams doing to prevent LLMs from gaming fixed eval prompts?
- Are there tools or libraries for programmatic prompt variation (like GSM-Symbolic but for non-math tasks)?
- What is the community consensus on how frequently eval prompts should be rotated?

**4. Cost-efficient eval design**
- Our platform evaluates free and paid models, with a hard $0.10 cost cap per eval run.
- What prompt length and output length combinations give the best signal-to-cost ratio?
- Are there any "canary prompts" — very short prompts with high discriminative power between model tiers?

**For each method or tool found, note:**
- Source (paper arXiv ID, GitHub repo URL with star count, blog post URL)
- Is it purely deterministic or does it use a neural component?
- Approximate implementation effort in Python (Easy / Medium / Hard)
- Real-world adoption evidence (used by which teams / companies / leaderboards)

Prioritise sources from: 2025–June 2026. Include GitHub repos with >100 stars. Flag anything described as "production-ready" or used by major AI labs.

---

## How to synthesise the two outputs

Once both reports are in `docs/research/`:

1. **Gemini gives you** — rigorous academic grounding, precise scoring formulas, arXiv citations
2. **Perplexity gives you** — what's actually being used, GitHub repos to fork, community consensus

Cross-reference: any method that appears in **both** reports (academic paper + real-world adoption) is a high-confidence candidate for v3 implementation. Methods in only one report need more validation before building.

Bring both reports into a session and ask Claude to produce a ranked implementation plan for v3 graders.
