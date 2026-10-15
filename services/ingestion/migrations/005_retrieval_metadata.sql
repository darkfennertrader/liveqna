ALTER TABLE ingestion.document_versions
ADD COLUMN retrieval_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE ingestion.document_versions
ADD COLUMN llm_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE ingestion.document_versions
ADD COLUMN content_profile TEXT;

UPDATE ingestion.document_versions AS version
SET content_profile = document.content_profile
FROM ingestion.documents AS document
WHERE document.id = version.document_id;

ALTER TABLE ingestion.document_versions
ALTER COLUMN content_profile SET NOT NULL;

ALTER TABLE ingestion.document_versions
ADD CONSTRAINT document_versions_content_profile_format CHECK (
    content_profile ~ '^[a-z][a-z0-9_]{0,99}$'
);

ALTER TABLE ingestion.document_versions
ADD CONSTRAINT document_versions_retrieval_metadata_object CHECK (
    jsonb_typeof(retrieval_metadata) = 'object'
);

ALTER TABLE ingestion.document_versions
ADD CONSTRAINT document_versions_llm_metadata_object CHECK (
    jsonb_typeof(llm_metadata) = 'object'
);
