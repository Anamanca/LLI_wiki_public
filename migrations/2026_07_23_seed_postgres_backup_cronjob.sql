-- Migration: Seed postgres-backup cron job entry so it appears in the admin Cron Jobs panel.
-- Uses ON CONFLICT DO NOTHING for safe re-runs.

INSERT INTO cron_jobs (job_id, name, description, schedule, job_type, managed, enabled, command)
VALUES (
  'postgres-backup',
  'PostgreSQL Backup',
  'Daily pg_dump -Fc + gzip of llm_wiki, uploaded to MinIO bucket llm-wiki-backups with 5-day retention',
  '0 0 * * *',
  'kubernetes_cronjob',
  true,
  true,
  'pg_dump -Fc | gzip → mc cp to MinIO → mc rm --older-than 5d'
) ON CONFLICT (job_id) DO NOTHING;
