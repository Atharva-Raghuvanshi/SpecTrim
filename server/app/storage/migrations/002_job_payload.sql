-- Revision jobs carry their feedback payload
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS payload JSONB;
