-- Migration 09: add 'structured_data' and 'code_exec' to the use_case CHECK constraint.
--
-- Drops the existing constraint (added in migration 06) and recreates with the
-- extended value set. Existing rows are unaffected.

DO $$
DECLARE
    cname text;
BEGIN
    SELECT conname INTO cname
    FROM pg_constraint
    WHERE conrelid = 'llm_evals'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%use_case%';
    IF cname IS NOT NULL THEN
        EXECUTE 'ALTER TABLE llm_evals DROP CONSTRAINT ' || quote_ident(cname);
    END IF;
END $$;

ALTER TABLE llm_evals
    ADD CONSTRAINT llm_evals_use_case_check
    CHECK (use_case IN (
        'reasoning', 'extraction', 'classification', 'summarization',
        'tool_use', 'structured_data', 'code_exec'
    ));

COMMENT ON COLUMN llm_evals.use_case IS
    'Eval category. v4 adds structured_data (StructEval) and code_exec (CRUXEval).';
