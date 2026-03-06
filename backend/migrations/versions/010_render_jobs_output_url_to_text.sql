-- Migration 010: Change render_jobs output_key and output_url from varchar to text
-- Signed GCS URLs with URL-encoded Japanese project names can exceed varchar(1000)

ALTER TABLE render_jobs ALTER COLUMN output_key TYPE TEXT;
ALTER TABLE render_jobs ALTER COLUMN output_url TYPE TEXT;
