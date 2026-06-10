-- Migration 013: Partial index on sequences, UNIQUE on assets
-- Issue: #287 - backend 小粒改善 6点
-- Note: GIN index on project_operations.affected_clips (#287 item 1) was originally
--       planned here, but it already exists as idx_project_operations_affected_clips
--       in 006_add_project_operations.sql — no action needed (review F1, PR #330).

-- (2) Partial index on sequences(project_id, is_default) WHERE is_default = TRUE
-- Covers the common query: WHERE project_id = ? AND is_default = TRUE
CREATE INDEX IF NOT EXISTS idx_sequences_project_id_is_default
    ON sequences (project_id, is_default) WHERE is_default = TRUE;

-- (6b) UNIQUE constraint on assets(project_id, name, type) to eliminate TOCTOU
-- Guard: if duplicate rows already exist, skip with a WARNING instead of failing startup.
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT project_id, name, type, COUNT(*) AS cnt
        FROM assets
        GROUP BY project_id, name, type
        HAVING COUNT(*) > 1
    ) AS dups;

    IF dup_count > 0 THEN
        RAISE WARNING
            'Migration 013: Found % group(s) of duplicate (project_id, name, type) in assets table. '
            'Skipping UNIQUE index creation. Resolve duplicates manually, then re-run to apply the constraint.',
            dup_count;
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_project_name_type_unique
            ON assets (project_id, name, type);
    END IF;
END $$;
