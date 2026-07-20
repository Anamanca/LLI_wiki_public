-- Migration: Add optional source_items columns used by the migrated ingestion pipeline.
-- These are optional forward-looking fields; the current code stores duration, language,
-- and classification inside transcript_json for backward compatibility.

ALTER TABLE source_items
    ADD COLUMN IF NOT EXISTS duration_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS language VARCHAR(10),
    ADD COLUMN IF NOT EXISTS classification_result JSONB DEFAULT NULL;

-- Also add a useful index for the worker queue and backfill queries.
CREATE INDEX IF NOT EXISTS ix_source_items_source_status_priority_published
    ON source_items (source_id, status, priority, published_at);
