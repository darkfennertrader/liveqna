ALTER TABLE ingestion.documents
ADD COLUMN deletion_status TEXT NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE ingestion.documents
ADD COLUMN deletion_error TEXT;

ALTER TABLE ingestion.documents
ADD COLUMN deletion_requested_at TIMESTAMPTZ;

ALTER TABLE ingestion.documents
ADD CONSTRAINT documents_deletion_status_valid CHECK (
    deletion_status IN ('ACTIVE', 'DELETING', 'DELETE_FAILED')
);

CREATE INDEX documents_deletion_status_idx
ON ingestion.documents(deletion_status);

CREATE TABLE ingestion.document_deletion_audit (
    document_id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    title TEXT NOT NULL,
    version_count INTEGER NOT NULL CHECK (version_count >= 0),
    deleted_by TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
