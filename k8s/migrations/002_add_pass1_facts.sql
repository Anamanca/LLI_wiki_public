-- Migration: source_items.pass1_facts (full Pass-1 fact store for provenance)
-- 2026-08-23
--
-- Purpose:
--   Persist the COMPLETE Pass-1 extraction result (entities, numbers, events,
--   relationships, key_claims, entity_relations + finance arrays) per source
--   item so reflect/verify (Pass 3) and future structured queries can read
--   provenance without re-running extraction. `key_claims`/`pass1_numbers`
--   remain as fast-access subsets.
--
-- Safe for re-run: ADD COLUMN IF NOT EXISTS; nullable; no backfill required.
-- Apply BEFORE deploying code that writes item.pass1_facts (release gate):
--   kubectl exec -n llm-wiki postgres-0 -- psql -U wiki -d llm_wiki \
--     -v lock_timeout=5000 -f - < k8s/migrations/002_add_pass1_facts.sql
-- Verify:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_name='source_items' AND column_name='pass1_facts';

ALTER TABLE source_items
    ADD COLUMN IF NOT EXISTS pass1_facts JSONB;

COMMENT ON COLUMN source_items.pass1_facts IS
    'Complete Pass-1 extraction facts (entities, numbers, events, claims, relations, finance arrays) — provenance for reflect verification and structured queries.';
