-- Migration 06: add 'tool_use' to the use_case CHECK constraint on llm_evals.
--
-- Postgres does not support ALTER ... MODIFY CONSTRAINT, so we drop the existing
-- auto-named constraint and recreate it with the extended value set.
-- Existing rows are unaffected; only new INSERTs are validated against the new list.

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
        'reasoning', 'extraction', 'classification', 'summarization', 'tool_use'
    ));

COMMENT ON COLUMN llm_evals.use_case IS
    'Eval category. v3 adds tool_use (function-calling evaluation via call_with_tools()).';
