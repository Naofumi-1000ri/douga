-- Migration: DB-backed idempotency enforcement for project_operations
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

-- Add partial UNIQUE index on idempotency_key (only for non-NULL values)
-- This prevents duplicate operations with the same key across all Cloud Run instances.
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_operations_idempotency_key_unique
    ON project_operations(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Drop the existing non-unique index on idempotency_key if it exists
-- (the unique index above covers lookups too, so the plain index is redundant)
DROP INDEX IF EXISTS idx_project_operations_idempotency_key;
