-- Migration 009: Add source_asset_id to assets table
-- Links extracted audio assets back to their source video asset

ALTER TABLE assets ADD COLUMN IF NOT EXISTS source_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_assets_source_asset_id ON assets(source_asset_id) WHERE source_asset_id IS NOT NULL;
