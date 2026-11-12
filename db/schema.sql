-- DataGuard schema (PostgreSQL 14+). All timestamps are timestamptz, written in UTC.
-- Idempotent: safe to run on an empty database; uses IF NOT EXISTS where practical.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- 1. datasets -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id    SERIAL PRIMARY KEY,
    dataset_name  VARCHAR(100) NOT NULL UNIQUE,
    source_type   VARCHAR(20)  NOT NULL CHECK (source_type IN ('csv', 'postgres', 'external')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 2. ingestion_batches ----------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_batches (
    batch_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id          INTEGER NOT NULL REFERENCES datasets(dataset_id),
    source_file         VARCHAR(255) NOT NULL,           -- original file name as uploaded
    file_name           VARCHAR(255) NOT NULL,           -- generated safe stored name
    file_size_bytes     BIGINT CHECK (file_size_bytes >= 0),
    file_sha256         CHAR(64),
    ingestion_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_records       INTEGER NOT NULL DEFAULT 0 CHECK (total_records >= 0),
    processing_status   VARCHAR(20) NOT NULL DEFAULT 'UPLOADED'
                        CHECK (processing_status IN ('UPLOADED', 'VALIDATING', 'COMPLETED', 'FAILED')),
    uploaded_by         VARCHAR(100) NOT NULL DEFAULT 'system',
    destination_table   VARCHAR(100),
    completed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_batches_dataset   ON ingestion_batches(dataset_id);
CREATE INDEX IF NOT EXISTS ix_batches_ingested  ON ingestion_batches(ingestion_timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_batches_sha256    ON ingestion_batches(file_sha256);

-- Reference data: customers (needed for referential integrity checks) ------
CREATE TABLE IF NOT EXISTS customers (
    customer_id    VARCHAR(50) PRIMARY KEY,
    customer_name  VARCHAR(200),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. transactions (raw text values so invalid data can be stored and reported)
CREATE TABLE IF NOT EXISTS transactions (
    record_id          BIGSERIAL PRIMARY KEY,
    batch_id           UUID NOT NULL REFERENCES ingestion_batches(batch_id) ON DELETE CASCADE,
    source_row_number  INTEGER NOT NULL CHECK (source_row_number >= 1),
    transaction_id     TEXT,
    customer_id        TEXT,
    transaction_amount TEXT,
    transaction_date   TEXT,
    merchant           TEXT,
    transaction_status TEXT,
    UNIQUE (batch_id, source_row_number)
);
CREATE INDEX IF NOT EXISTS ix_txn_batch          ON transactions(batch_id);
CREATE INDEX IF NOT EXISTS ix_txn_batch_txnid    ON transactions(batch_id, transaction_id);
CREATE INDEX IF NOT EXISTS ix_txn_customer       ON transactions(customer_id);

-- 4. validation_rules -----------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_rules (
    rule_id          SERIAL PRIMARY KEY,
    rule_code        VARCHAR(20)  NOT NULL UNIQUE,
    rule_name        VARCHAR(100) NOT NULL,
    rule_category    VARCHAR(30)  NOT NULL
                     CHECK (rule_category IN ('COMPLETENESS', 'UNIQUENESS', 'VALIDITY',
                                              'CONSISTENCY', 'REFERENTIAL_INTEGRITY')),
    rule_description TEXT NOT NULL,
    severity         VARCHAR(10)  NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')),
    parameters       JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE
);

-- 5. validation_results ---------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_results (
    result_id         BIGSERIAL PRIMARY KEY,
    batch_id          UUID   NOT NULL REFERENCES ingestion_batches(batch_id) ON DELETE CASCADE,
    record_id         BIGINT NOT NULL REFERENCES transactions(record_id) ON DELETE CASCADE,
    record_identifier TEXT,                       -- business ID (may be null/duplicated)
    row_number        INTEGER,
    rule_id           INTEGER NOT NULL REFERENCES validation_rules(rule_id),
    column_name       VARCHAR(100),
    offending_value   TEXT,
    error_description TEXT NOT NULL,
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_vr_batch        ON validation_results(batch_id);
CREATE INDEX IF NOT EXISTS ix_vr_batch_rule   ON validation_results(batch_id, rule_id);
CREATE INDEX IF NOT EXISTS ix_vr_record       ON validation_results(record_id);
CREATE INDEX IF NOT EXISTS ix_vr_identifier   ON validation_results(record_identifier);

-- 6. quality_metrics (one row per calculation; history retained) -----------
CREATE TABLE IF NOT EXISTS quality_metrics (
    metric_id          SERIAL PRIMARY KEY,
    batch_id           UUID NOT NULL REFERENCES ingestion_batches(batch_id) ON DELETE CASCADE,
    completeness_score NUMERIC(5,2) CHECK (completeness_score BETWEEN 0 AND 100),
    uniqueness_score   NUMERIC(5,2) CHECK (uniqueness_score   BETWEEN 0 AND 100),
    validity_score     NUMERIC(5,2) CHECK (validity_score     BETWEEN 0 AND 100),
    consistency_score  NUMERIC(5,2) CHECK (consistency_score  BETWEEN 0 AND 100),
    overall_score      NUMERIC(5,2) CHECK (overall_score      BETWEEN 0 AND 100),
    total_records      INTEGER NOT NULL DEFAULT 0 CHECK (total_records   >= 0),
    valid_records      INTEGER NOT NULL DEFAULT 0 CHECK (valid_records   >= 0),
    invalid_records    INTEGER NOT NULL DEFAULT 0 CHECK (invalid_records >= 0),
    calculated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_qm_batch ON quality_metrics(batch_id, calculated_at DESC);

-- 7. audit_logs -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id            BIGSERIAL PRIMARY KEY,
    batch_id          UUID REFERENCES ingestion_batches(batch_id) ON DELETE SET NULL,
    action            VARCHAR(50) NOT NULL,
    actor             VARCHAR(100) NOT NULL DEFAULT 'system',
    action_timestamp  TIMESTAMPTZ NOT NULL DEFAULT now(),
    execution_status  VARCHAR(20) NOT NULL CHECK (execution_status IN ('STARTED', 'SUCCESS', 'FAILED')),
    rules_executed    JSONB,
    records_processed INTEGER CHECK (records_processed >= 0),
    failure_count     INTEGER CHECK (failure_count >= 0),
    duration_ms       INTEGER CHECK (duration_ms >= 0),
    details           JSONB
);
CREATE INDEX IF NOT EXISTS ix_audit_batch ON audit_logs(batch_id);
CREATE INDEX IF NOT EXISTS ix_audit_time  ON audit_logs(action_timestamp DESC);

-- 8. exception_reviews (append-only: each status change is a new row) -------
CREATE TABLE IF NOT EXISTS exception_reviews (
    review_id     BIGSERIAL PRIMARY KEY,
    result_id     BIGINT NOT NULL REFERENCES validation_results(result_id) ON DELETE CASCADE,
    review_status VARCHAR(15) NOT NULL DEFAULT 'OPEN'
                  CHECK (review_status IN ('OPEN', 'IN_REVIEW', 'REVIEWED', 'WAIVED')),
    reviewer      VARCHAR(100),
    comments      TEXT,
    reviewed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_review_result ON exception_reviews(result_id, reviewed_at DESC);

-- Append-only enforcement ---------------------------------------------------
-- The audit trail and review history must not be edited or deleted. The application never
-- does either; these triggers make the database refuse it too. This stops accidental or
-- application-level tampering; a database owner could still drop the triggers deliberately.
-- Consequence: a batch that has audit or review history cannot be deleted.
CREATE OR REPLACE FUNCTION dg_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: the table is append-only', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;
CREATE TRIGGER trg_audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION dg_block_mutation();

DROP TRIGGER IF EXISTS trg_exception_reviews_append_only ON exception_reviews;
CREATE TRIGGER trg_exception_reviews_append_only BEFORE UPDATE OR DELETE ON exception_reviews
    FOR EACH ROW EXECUTE FUNCTION dg_block_mutation();

COMMIT;
