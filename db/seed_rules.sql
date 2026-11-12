-- Seed validation rules and the transactions / customers datasets.
-- Rule codes map to Python rule classes (added in Module 2). Re-runnable.

INSERT INTO datasets (dataset_name, source_type) VALUES
    ('transactions', 'csv'),
    ('customers',    'csv')
ON CONFLICT (dataset_name) DO NOTHING;

INSERT INTO validation_rules (rule_code, rule_name, rule_category, rule_description, severity, parameters) VALUES
 ('C01','Transaction ID required',     'COMPLETENESS','transaction_id must not be null or blank','HIGH',  '{"column":"transaction_id"}'),
 ('C02','Customer ID required',        'COMPLETENESS','customer_id must not be null or blank','HIGH',     '{"column":"customer_id"}'),
 ('C03','Amount required',             'COMPLETENESS','transaction_amount must not be null or blank','HIGH','{"column":"transaction_amount"}'),
 ('C04','Date required',               'COMPLETENESS','transaction_date must not be null or blank','HIGH','{"column":"transaction_date"}'),
 ('C05','Status required',             'COMPLETENESS','transaction_status must not be null or blank','HIGH','{"column":"transaction_status"}'),
 ('C06','Merchant required',           'COMPLETENESS','merchant must not be null or blank','MEDIUM',      '{"column":"merchant"}'),
 ('U01','Duplicate transaction ID',    'UNIQUENESS',  'transaction_id appears more than once in the batch (first occurrence kept, later ones flagged)','CRITICAL','{"column":"transaction_id"}'),
 ('U02','Duplicate record',            'UNIQUENESS',  'Row is identical to an earlier row across all business columns','HIGH','{}'),
 ('V01','Amount numeric',              'VALIDITY',    'transaction_amount must be a valid number','HIGH','{"column":"transaction_amount"}'),
 ('V02','Amount positive',             'VALIDITY',    'transaction_amount must be greater than zero','HIGH','{"column":"transaction_amount","min_exclusive":0}'),
 ('V03','Date format',                 'VALIDITY',    'transaction_date must be a valid date in YYYY-MM-DD format','HIGH','{"column":"transaction_date","format":"%Y-%m-%d"}'),
 ('V04','Allowed status',              'VALIDITY',    'transaction_status must be one of the allowed values','MEDIUM','{"column":"transaction_status","allowed":["COMPLETED","PENDING","FAILED","REVERSED"]}'),
 ('K01','Date not in future',          'CONSISTENCY', 'transaction_date must not be later than the ingestion date','MEDIUM','{"column":"transaction_date"}'),
 ('K02','Amount ceiling',              'CONSISTENCY', 'transaction_amount must not exceed the configured ceiling','MEDIUM','{"column":"transaction_amount","max":1000000}'),
 ('R01','Customer exists',             'REFERENTIAL_INTEGRITY','customer_id must exist in the customers reference table (orphan check)','HIGH','{"column":"customer_id","reference_table":"customers","reference_column":"customer_id"}')
ON CONFLICT (rule_code) DO NOTHING;
