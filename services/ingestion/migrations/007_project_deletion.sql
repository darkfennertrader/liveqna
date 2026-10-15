ALTER TABLE ingestion.projects
ADD COLUMN deletion_status TEXT NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE ingestion.projects
ADD COLUMN deletion_error TEXT;

ALTER TABLE ingestion.projects
ADD COLUMN deletion_requested_at TIMESTAMPTZ;

ALTER TABLE ingestion.projects
ADD CONSTRAINT projects_deletion_status_valid CHECK (
    deletion_status IN ('ACTIVE', 'DELETING', 'DELETE_FAILED')
);

CREATE INDEX projects_deletion_status_idx
ON ingestion.projects(deletion_status);
