-- Migration 011: Add is_auto flag to sequence_snapshots
-- Distinguishes automatically created snapshots from user-created ones

ALTER TABLE sequence_snapshots ADD COLUMN IF NOT EXISTS is_auto BOOLEAN NOT NULL DEFAULT false;

-- Index for efficient querying of auto snapshots per sequence
CREATE INDEX IF NOT EXISTS idx_sequence_snapshots_seq_auto
  ON sequence_snapshots(sequence_id, is_auto, created_at DESC);
