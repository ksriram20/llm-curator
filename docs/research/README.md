# Research

This folder holds external research that informs llm-curator's design decisions.

## Contents

| File | Source | Purpose |
|---|---|---|
| `grading-methods-gemini-deepresearch.md` *(place here)* | Google Gemini Deep Research | arXiv survey of deterministic LLM grading methods — input for v0.2 grader upgrade |

---

## How to use these files

1. Drop the Gemini Deep Research output as `grading-methods-gemini-deepresearch.md` in this folder
2. Share it in the session — the findings will be used to replace/augment the shallow graders in `eval_prompts.py`
3. Once graders are updated, note the paper references and chosen methods in `DEVLOG.md`
