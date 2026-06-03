-- ── Migration 05: add grader_version to llm_evals ──────────────────────────
-- Tracks which version of the grading suite produced each eval score.
-- Old rows default to 'v1' (the baseline graders). Scores are NOT comparable
-- across grader versions — always filter by grader_version when building
-- leaderboards or trend charts.

ALTER TABLE llm_evals
    ADD COLUMN IF NOT EXISTS grader_version TEXT NOT NULL DEFAULT 'v1';

-- Index for leaderboard queries that filter by version
CREATE INDEX IF NOT EXISTS idx_llm_evals_grader_version
    ON llm_evals(grader_version);

COMMENT ON COLUMN llm_evals.grader_version IS
    'Grader suite version that produced this score. '
    'v1=baseline, v2=SymPy+DOC+QuasiExact+IFEval+ROUGE-K. '
    'Do not compare scores across versions.';
