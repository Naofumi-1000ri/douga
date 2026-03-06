-- Migration 008: Add sequence_snapshots table
-- Stores checkpoint history for sequences (replacing standalone sessions/snapshots)

CREATE TABLE IF NOT EXISTS sequence_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence_id UUID NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    timeline_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sequence_snapshots_sequence_id ON sequence_snapshots(sequence_id);
