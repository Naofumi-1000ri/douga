-- Migration: Add api_keys table for API key authentication
-- This allows Firebase-authenticated users to generate long-lived API keys
-- for programmatic access (e.g., MCP Server)

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    key_prefix VARCHAR(20) NOT NULL,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS ix_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys(user_id);
