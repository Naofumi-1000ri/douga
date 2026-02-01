-- Migration: Add ai_provider column to projects table
-- This allows users to set their preferred AI provider (OpenAI, Gemini, Anthropic) per project

ALTER TABLE projects ADD COLUMN IF NOT EXISTS ai_provider VARCHAR(50) DEFAULT NULL;

-- Add comment for documentation
COMMENT ON COLUMN projects.ai_provider IS 'Preferred AI provider for this project: openai, gemini, or anthropic';
