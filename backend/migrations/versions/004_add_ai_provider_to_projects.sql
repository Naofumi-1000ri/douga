-- Migration: Add AI settings columns to projects table
-- ai_api_key: Project-specific API key for AI services
-- ai_provider: Preferred AI provider (OpenAI, Gemini, Anthropic) per project

ALTER TABLE projects ADD COLUMN IF NOT EXISTS ai_api_key VARCHAR(500) DEFAULT NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS ai_provider VARCHAR(50) DEFAULT NULL;

-- Add comments for documentation
COMMENT ON COLUMN projects.ai_api_key IS 'Project-specific API key for AI assistant';
COMMENT ON COLUMN projects.ai_provider IS 'Preferred AI provider for this project: openai, gemini, or anthropic';
