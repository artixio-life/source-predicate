CREATE SCHEMA IF NOT EXISTS source;

-- Shared with the source-information crawler. If this database already has
-- source.country (e.g. seeded by source-information), this is a no-op.
CREATE TABLE IF NOT EXISTS source.country(
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    code VARCHAR(2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_country_name ON source.country(name);
CREATE INDEX IF NOT EXISTS idx_country_code ON source.country(code);


-- document_url is TEXT[] (not a single VARCHAR): a record can carry more
-- than one related document URL (e.g. PAR + PIL + SPC for the same
-- product) instead of exploding into one row per document.
CREATE TABLE IF NOT EXISTS source.drug_predicate_raw_records (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255),
  country_id INTEGER REFERENCES source.country(id) ON DELETE SET NULL,
  document_url TEXT[],
  json_data JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drug_predicate_raw_country ON source.drug_predicate_raw_records(country_id);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_raw_name ON source.drug_predicate_raw_records(name);

-- One row per (country, document_url array): lets crawlers upsert on
-- re-crawl instead of accumulating duplicate rows for the same document(s)
-- on every run. Dedup against an individual URL (regardless of which row's
-- array holds it) is done in app.db.check_record_exists_by_url via ANY().
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'unique_drug_predicate_country_document'
    ) THEN
        ALTER TABLE source.drug_predicate_raw_records
        ADD CONSTRAINT unique_drug_predicate_country_document UNIQUE (country_id, document_url);
    END IF;
END$$;


-- Per-run crawl log: one row per country each time it is crawled. A row is
-- inserted when the country starts (started_at) and updated with
-- finished_at + status when it ends.
CREATE TABLE IF NOT EXISTS source.drug_predicate_crawl_log (
    id SERIAL PRIMARY KEY,
    country_id INTEGER REFERENCES source.country(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'START',  -- START | DONE | FAILED | SKIPPED
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_drug_predicate_crawl_log_country_id
    ON source.drug_predicate_crawl_log(country_id);

CREATE INDEX IF NOT EXISTS idx_drug_predicate_crawl_log_started_at
    ON source.drug_predicate_crawl_log(started_at);
