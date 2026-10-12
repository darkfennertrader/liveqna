CREATE TYPE ingestion.document_status AS ENUM (
    'DRAFT',
    'APPROVED',
    'ARCHIVED'
);

CREATE TYPE ingestion.processing_status AS ENUM (
    'QUEUED',
    'RUNNING',
    'READY',
    'FAILED'
);

CREATE TABLE ingestion.projects (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion.documents (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES ingestion.projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (
        document_type IN ('official', 'internal', 'transcript', 'artifact')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, title)
);

CREATE TABLE ingestion.document_versions (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES ingestion.documents(id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    status ingestion.document_status NOT NULL DEFAULT 'DRAFT',
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    approved_by TEXT,
    supersedes_version_id UUID REFERENCES ingestion.document_versions(id),
    UNIQUE (document_id, version_label),
    UNIQUE (document_id, content_hash)
);

CREATE UNIQUE INDEX one_approved_version_per_document
ON ingestion.document_versions(document_id)
WHERE status = 'APPROVED';

CREATE TABLE ingestion.ingestion_runs (
    id UUID PRIMARY KEY,
    document_version_id UUID NOT NULL
        REFERENCES ingestion.document_versions(id) ON DELETE CASCADE,
    processing_status ingestion.processing_status NOT NULL DEFAULT 'QUEUED',
    extractor_version TEXT,
    chunking_version TEXT,
    embedding_model TEXT,
    chunk_count INTEGER CHECK (chunk_count IS NULL OR chunk_count >= 0),
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion.chunks (
    id UUID PRIMARY KEY,
    document_version_id UUID NOT NULL
        REFERENCES ingestion.document_versions(id) ON DELETE CASCADE,
    parent_section_id TEXT,
    sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
    text TEXT NOT NULL,
    source_locator JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    qdrant_point_id UUID NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_version_id, sequence_number),
    UNIQUE (document_version_id, content_hash)
);

CREATE INDEX document_versions_status_idx
ON ingestion.document_versions(status);

CREATE INDEX ingestion_runs_version_idx
ON ingestion.ingestion_runs(document_version_id, created_at DESC);

CREATE INDEX chunks_version_idx
ON ingestion.chunks(document_version_id, sequence_number);
