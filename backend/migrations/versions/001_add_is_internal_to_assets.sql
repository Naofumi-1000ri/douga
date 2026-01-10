-- Migration: Add is_internal column to assets table
-- This column marks assets that are internal (e.g., extracted audio from video)
-- and should not be shown to users in the asset library

ALTER TABLE assets ADD COLUMN IF NOT EXISTS is_internal BOOLEAN DEFAULT FALSE;

-- Update any existing extracted audio assets to be marked as internal
-- (These are audio files that have the same name pattern as video files, minus extension)
-- This is optional and can be customized based on your needs
