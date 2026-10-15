ALTER TABLE ingestion.documents
ADD COLUMN content_profile TEXT NOT NULL DEFAULT 'general_document';

ALTER TABLE ingestion.documents
ADD CONSTRAINT documents_content_profile_format CHECK (
    content_profile ~ '^[a-z][a-z0-9_]{0,99}$'
);

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN renderer_version TEXT;

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN vision_provider TEXT;

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN vision_model TEXT;

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN vision_prompt_version TEXT;
