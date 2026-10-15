ALTER TABLE ingestion.ingestion_runs
ADD COLUMN source_format TEXT;

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN normalized_path TEXT;

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN quality_report JSONB;

ALTER TABLE ingestion.ingestion_runs
ADD COLUMN error_code TEXT;
