-- Migration: Add asset folders table and folder_id to assets
-- Date: 2026-01-29

-- Create asset_folders table
CREATE TABLE IF NOT EXISTS asset_folders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Create index on project_id for faster lookups
CREATE INDEX IF NOT EXISTS idx_asset_folders_project_id ON asset_folders(project_id);

-- Add folder_id column to assets table
ALTER TABLE assets ADD COLUMN IF NOT EXISTS folder_id UUID REFERENCES asset_folders(id) ON DELETE SET NULL;

-- Create index on folder_id for faster lookups
CREATE INDEX IF NOT EXISTS idx_assets_folder_id ON assets(folder_id);
