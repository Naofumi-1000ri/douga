-- Migration 012: DB-backed idempotency enforcement for project_operations
-- Issue: #264 - インスタンスローカル状態が Cloud Run で機能しない
-- Date: 2026-06-10

-- Add response storage columns to project_operations
-- These columns store the persisted response body/status for idempotency replay.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_operations' AND column_name = 'response_status_code'
    ) THEN
        ALTER TABLE project_operations ADD COLUMN response_status_code INTEGER;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_operations' AND column_name = 'response_body'
    ) THEN
        ALTER TABLE project_operations ADD COLUMN response_body JSONB;
    END IF;
END $$;

-- Add a partial UNIQUE index scoped to (user_id, idempotency_key).
-- Scoping by user_id prevents one user from replaying another user's response
-- when they happen to send the same Idempotency-Key value (information leak).
-- The partial predicate keeps the constraint off rows that have no key.
-- Drop the older key-only unique index first if a previous migration created it.
DROP INDEX IF EXISTS idx_project_operations_idempotency_key_unique;

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_operations_idempotency_key_unique
    ON project_operations(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Drop the existing non-unique index on idempotency_key if it exists
-- (the unique index above covers lookups too, so the plain index is redundant)
DROP INDEX IF EXISTS idx_project_operations_idempotency_key;
