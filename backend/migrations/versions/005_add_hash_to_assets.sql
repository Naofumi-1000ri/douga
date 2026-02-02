-- Migration: Add hash column to assets table
-- This column stores SHA-256 hash of asset files for fingerprint matching in session files
-- Format: "sha256:<hex_digest>"

ALTER TABLE assets ADD COLUMN IF NOT EXISTS hash VARCHAR(100);

-- Index for fast lookup by hash during session loading
CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets(hash) WHERE hash IS NOT NULL;
