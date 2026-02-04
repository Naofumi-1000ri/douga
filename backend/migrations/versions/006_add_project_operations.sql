-- Migration: Add project_operations table for operation history and rollback
-- Date: 2026-02-04

CREATE TABLE IF NOT EXISTS project_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    -- Operation metadata
    operation_type VARCHAR(50) NOT NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'api_v1',

    -- Affected entities (JSONB arrays for efficient querying)
    affected_clips JSONB DEFAULT '[]'::jsonb NOT NULL,
    affected_layers JSONB DEFAULT '[]'::jsonb NOT NULL,
    affected_audio_clips JSONB DEFAULT '[]'::jsonb NOT NULL,

    -- Diff and summaries
    diff JSONB,
    request_summary JSONB,
    result_summary JSONB,

    -- Rollback support
    rollback_data JSONB,
    rollback_available BOOLEAN DEFAULT TRUE NOT NULL,
    rolled_back BOOLEAN DEFAULT FALSE NOT NULL,
    rolled_back_at TIMESTAMP WITH TIME ZONE,
    rolled_back_by UUID,

    -- Operation result
    success BOOLEAN NOT NULL,
    error_code VARCHAR(50),
    error_message TEXT,

    -- Request context
    idempotency_key VARCHAR(100),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,

    -- Timestamp
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_project_operations_project_id
    ON project_operations(project_id);
CREATE INDEX IF NOT EXISTS idx_project_operations_operation_type
    ON project_operations(operation_type);
CREATE INDEX IF NOT EXISTS idx_project_operations_created_at
    ON project_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_operations_idempotency_key
    ON project_operations(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_project_operations_user_id
    ON project_operations(user_id);

-- GIN index for efficient JSONB array queries (e.g., find operations affecting a specific clip)
CREATE INDEX IF NOT EXISTS idx_project_operations_affected_clips
    ON project_operations USING GIN (affected_clips);
CREATE INDEX IF NOT EXISTS idx_project_operations_affected_layers
    ON project_operations USING GIN (affected_layers);
CREATE INDEX IF NOT EXISTS idx_project_operations_affected_audio_clips
    ON project_operations USING GIN (affected_audio_clips);
