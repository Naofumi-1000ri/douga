-- Migration: Add thumbnail_storage_key to projects table
-- Date: 2026-02-05
-- Purpose: Store GCS storage key instead of signed URL to avoid String(500) limit

-- Add thumbnail_storage_key column
ALTER TABLE projects ADD COLUMN IF NOT EXISTS thumbnail_storage_key VARCHAR(500);

-- Comment for documentation
COMMENT ON COLUMN projects.thumbnail_storage_key IS 'GCS storage key for project thumbnail (not signed URL)';
