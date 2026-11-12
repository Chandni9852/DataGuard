# DataGuard
### Automated Data Quality & Governance Monitoring Platform

DataGuard is a data quality and governance monitoring platform designed to ingest datasets, validate records against configurable business rules, calculate data quality scores, manage exceptions, and maintain an auditable history of data processing.

Built using Python, FastAPI, PostgreSQL, and SQLAlchemy, the project demonstrates how data quality checks and governance workflows can be implemented in a structured, API-driven system.

**Project Status:** Steps 1–7 completed. Streamlit dashboard and deployment are planned for future development.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Database Design](#database-design)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Sample Data](#sample-data)
- [API Reference](#api-reference)
- [Data Quality Scoring](#data-quality-scoring)
- [Exception Management](#exception-management)
- [Audit Logging and Data Lineage](#audit-logging-and-data-lineage)
- [Testing](#testing)
- [Known Limitations](#known-limitations)
- [Development Roadmap](#development-roadmap)

---

## Overview

Data quality problems such as missing values, duplicate records, invalid formats, and inconsistent information can affect the reliability of downstream analytics and business decisions.

DataGuard addresses these challenges through a structured data quality and governance workflow.

The platform uses synthetic financial transaction data to demonstrate:

1. Dataset ingestion and batch tracking.
2. Configurable data validation.
3. Automated data quality scoring.
4. Exception identification and review.
5. Audit logging and source-to-record lineage tracking.

The system preserves original input values so that data issues can be identified and investigated without silently modifying the source records.

## Key Features

### 1. Dataset Ingestion
- Upload CSV datasets through REST APIs.
- Preview and profile files before ingestion.
- Track ingestion batches and processing status.
- Detect duplicate file uploads using SHA-256 hashes.
- Preserve original uploaded files for lineage verification.
- Record failed ingestion attempts.

### 2. Configurable Validation Engine
- Execute configurable validation rules against ingested records.
- Support completeness, uniqueness, validity, consistency, and referential-integrity checks.
- Store validation failures with rule-level details.
- Configure rule severity, activation status, and parameters.
- Track batch validation status and results.

### 3. Automated Data Quality Scoring
- Calculate completeness, uniqueness, validity, and consistency scores.
- Generate an overall Data Quality Score (DQS).
- Maintain historical score records.
- Support score recalculation and quality trend retrieval.
- Preserve the rules that actually ran for each batch when recalculating scores.

### 4. Exception Management
- Identify and retrieve failed validation records.
- Filter exceptions by batch, category, severity, rule, and review status.
- Search, sort, and paginate exception records.
- Review individual exceptions or perform bulk reviews.
- Maintain append-only review history.
- Export exception reports as CSV.

### 5. Audit Logging and Data Lineage
- Maintain an auditable record of system operations.
- Track ingestion, validation, scoring, rule updates, reviews, and exports.
- Trace batches back to their original uploaded files.
- Verify source files using SHA-256 hashes.
- Trace individual validation failures to their original source rows.
- Enforce append-only behavior for audit and review records using PostgreSQL triggers.

---

## Technology Stack

| Category | Technologies |
|---|---|
| Programming Language | Python |
| Backend Framework | FastAPI |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.x |
| Data Processing | Pandas, NumPy |
| API Testing | Pytest, HTTPX |
| Database Connectivity | Psycopg |
| Containerization | Docker Compose |
| API Documentation | Swagger / OpenAPI |
| Version Control | Git, GitHub |

---

## Architecture

DataGuard follows a layered backend architecture.

```text
                Client / API Consumer
                         |
                         v
                  FastAPI Application
                         |
                         v
                    API Routes
                         |
                         v
                   Service Layer
                         |
              +----------+----------+
              |                     |
              v                     v
       Validation Engine      Governance Logic
              |                     |
              +----------+----------+
                         |
                         v
                   SQLAlchemy ORM
                         |
                         v
                    PostgreSQL
```

**Layer responsibilities:**

- **API Layer:** Handles HTTP requests, input validation, and responses.
- **Service Layer:** Implements ingestion, validation, scoring, exception management, and audit workflows.
- **Validation Engine:** Executes registered validation rules against dataset records.
- **Database Layer:** Stores datasets, batches, transactions, validation results, scores, and audit history.
- **PostgreSQL:** Provides persistent storage and database-level enforcement of append-only records.

The API is the application interface for external clients. The database is accessed through the backend rather than directly by the presentation layer.

---

## Database Design

The database is organized around the following core entities:

| Table | Purpose |
|---|---|
| `datasets` | Stores registered dataset information |
| `ingestion_batches` | Tracks uploaded files and ingestion batches |
| `customers` | Stores customer reference records |
| `transactions` | Stores ingested transaction records |
| `validation_rules` | Stores configurable validation rules |
| `validation_results` | Stores individual validation failures |
| `quality_metrics` | Stores calculated quality scores |
| `audit_logs` | Maintains system activity records |
| `exception_reviews` | Maintains exception review history |

The database schema is maintained in `db/schema.sql`, which serves as the source of truth for DDL.

---

## Project Structure

```text
DataGuard/
│
├── backend/
│   ├── main.py
│   ├── api/
│   ├── services/
│   └── ...
│
├── data/
│   ├── generate_sample_data.py
│   ├── customers.csv
│   ├── sample_transactions.csv
│   ├── sample_transactions_clean.csv
│   └── sample_transactions_answer_key.json
│
├── db/
│   └── schema.sql
│
├── docs/
│
├── frontend/
│
├── tests/
│   ├── unit/
│   └── ...
│
├── .env.example
├── .gitignore
├── docker-compose.yml
├── requirements.txt
└── README.md
```

*The tree is illustrative. Refer to the repository for the complete file structure.*

---

## Getting Started


### Prerequisites

- Python 3.13.12 (current development environment).
- Docker Desktop with Docker Compose.
- Git (for cloning the repository).


Dependencies are listed in `requirements.txt`.
Package versions are specified using minimum-version constraints.

The PostgreSQL container initializes the database using two scripts:

- `db/schema.sql` creates the database tables, indexes, and triggers.
- `db/seed_rules.sql` inserts the initial datasets and 15 validation rules.

These scripts execute automatically only when PostgreSQL initializes
an empty data volume. They are not rerun on ordinary container restarts.

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/DataGuard.git
cd DataGuard
```

Replace `YOUR_USERNAME` with the GitHub account that hosts the repository.

### 2. Create a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a local `.env` file from the example.

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
```

**Linux / macOS:**

```bash
cp .env.example .env
```

Edit `.env` and configure the required database credentials.

Do not commit your actual `.env` file or credentials to GitHub.

### 5. Start PostgreSQL

```bash
docker compose --env-file .env up -d db
```

This starts the PostgreSQL service and initializes the database according to the configured Docker and schema setup.

### 6. Start the FastAPI application

```bash
uvicorn backend.main:app --reload
```

The API should be available at:

- **Health check:** http://127.0.0.1:8000/api/v1/health
- **Interactive API documentation:** http://127.0.0.1:8000/docs

---

## Sample Data

DataGuard uses reproducible synthetic financial transaction data. No real customer or financial information is required.

Generate sample data using:

```bash
python data/generate_sample_data.py --rows 1000 --seed 42
```

The sample dataset includes:

| File | Purpose |
|---|---|
| `customers.csv` | Reference data for referential-integrity validation |
| `sample_transactions_clean.csv` | Dataset in which every row passes the configured rules |
| `sample_transactions.csv` | Dataset containing intentionally injected defects |
| `sample_transactions_answer_key.json` | Expected rule violations and quality scores |

The test suite includes `tests/unit/reference_oracle.py`, which independently re-derives validation failures from the CSV data to cross-check the answer key.

### Ingestion order

Load customer reference data before transaction data.

```bash
curl -F file=@data/customers.csv http://127.0.0.1:8000/api/v1/datasets/2/batches
```

Then ingest transactions:

```bash
curl -F file=@data/sample_transactions.csv http://127.0.0.1:8000/api/v1/datasets/1/batches
```

### Ingestion behaviour

- Original field values are preserved. Only completely empty cells become `NULL`.
- Re-uploading an identical file for the same dataset returns HTTP `409` with the existing batch ID.
- Use `force=true` to explicitly ingest an identical file again.
- Failed ingestion does not leave partial data.
- Original uploads are retained under `uploads/<batch_id>.csv` for lineage verification.

---

## API Reference

All endpoints below use the `/api/v1` prefix.

### Dataset Ingestion

| Method | Endpoint | Description |
|---|---|---|
| GET | `/datasets` | List registered datasets |
| POST | `/datasets/{id}/preview` | Preview and profile a CSV without saving it |
| POST | `/datasets/{id}/batches` | Ingest a CSV as a new batch |
| GET | `/batches` | Retrieve batches with pagination |
| GET | `/batches/{batch_id}` | Retrieve batch details |

### Validation Engine

| Method | Endpoint | Description |
|---|---|---|
| POST | `/batches/{batch_id}/validate` | Run active validation rules |
| GET | `/batches/{batch_id}/validation-summary` | Retrieve validation summary |
| GET | `/rules` | List validation rules |
| PATCH | `/rules/{rule_id}` | Update rule configuration |

Rules are implemented in `backend/services/rules/`, with one class per validation check.

Rule registration uses the `@register("X01")` pattern. Each rule's activation status, severity, and parameters are maintained in the `validation_rules` table.

### Quality Scoring

| Method | Endpoint | Description |
|---|---|---|
| GET | `/batches/{batch_id}/quality` | Retrieve current batch quality scores |
| GET | `/batches/{batch_id}/quality/history` | Retrieve score calculation history |
| POST | `/batches/{batch_id}/quality/recalculate` | Recalculate scores from stored results |
| GET | `/metrics/trend` | Retrieve quality trends across batches |

### Exception Management

| Method | Endpoint | Description |
|---|---|---|
| GET | `/exceptions` | List and filter validation failures |
| GET | `/exceptions/summary` | Retrieve exception counts |
| GET | `/exceptions/{id}` | Retrieve exception details and review history |
| POST | `/exceptions/{id}/review` | Review an individual exception |
| POST | `/exceptions/review-bulk` | Review multiple exceptions |
| GET | `/exceptions/{id}/history` | Retrieve review history |
| GET | `/reports/exceptions.csv` | Export exception records as CSV |

Supported exception filters include:

- `batch_id`
- `category`
- `severity`
- `rule_code`
- `review_status`
- `q` (record ID search)

Pagination uses `limit` and `offset`. Sorting supports severity, detection time, row, and rule. The total result count is returned through `X-Total-Count`.

### Audit Logging and Lineage

| Method | Endpoint | Description |
|---|---|---|
| GET | `/audit-logs` | Retrieve filtered audit records |
| GET | `/audit-logs/actions` | Retrieve distinct audit action names |
| GET | `/audit-logs/{log_id}` | Retrieve an individual audit entry |
| GET | `/batches/{batch_id}/lineage` | Retrieve batch lineage information |
| GET | `/exceptions/{result_id}/lineage` | Trace a validation failure to its source |

---

## Data Quality Scoring

DataGuard calculates four quality dimensions and an overall Data Quality Score (DQS).

### Scoring methodology

| Dimension | Calculation |
|---|---|
| Completeness | Non-missing mandatory cells / total mandatory cells × 100 |
| Uniqueness | Records without uniqueness failures / total records × 100 |
| Validity | Records without validity failures / total records × 100 |
| Consistency | Records without consistency or referential-integrity failures / total records × 100 |

**Overall DQS** is calculated as the equal-weighted mean of the four dimensions.

Additional scoring behaviour:

- Each record is counted once per dimension, regardless of how many rules it fails in that dimension.
- Each missing cell is counted once.
- Scores are rounded half-up to two decimal places.
- Overall DQS is calculated from the rounded dimension scores.
- Empty batches return `status: "no_data"` rather than a score of 100%.
- If all rules in a dimension are disabled, that dimension is `null` and excluded from the overall average.
- Recalculation uses the rules that actually ran for the batch, rather than the current rule configuration.

---

## Validation Rules

The validation engine supports the following rule categories:

| Category | Rule IDs | Purpose |
|---|---|---|
| Completeness | C01–C06 | Identify missing mandatory values |
| Uniqueness | U01–U02 | Detect duplicate transaction identifiers and duplicate records |
| Validity | V01–V04 | Check amount, date, and status validity |
| Consistency | K01–K02 | Check date and configured amount constraints |
| Referential Integrity | R01 | Verify customer references |

### Important rule behaviour

- A value is considered missing if it is `NULL`, empty, or whitespace-only.
- Other rules skip missing values so that the same missing-value defect is not reported repeatedly.
- U01 flags the second and subsequent occurrences of a `transaction_id`.
- U02 flags records identical across all six columns.
- V01 checks whether amount is a plain decimal number.
- V02 checks whether amount is greater than zero.
- V03 checks whether the date is a valid `YYYY-MM-DD` date.
- V04 checks status using case-sensitive matching.
- K01 checks that the transaction date is not after the batch ingestion date.
- K02 checks that amount does not exceed the configured maximum.
- R01 checks whether the customer exists in the customers table.

Validation is refused with HTTP `409` if the customer reference table is empty.

### Batch lifecycle

```text
UPLOADED
    |
    v
VALIDATING
    |
    +------> COMPLETED
    |
    +------> FAILED
```

A failed batch can be retried. A completed batch cannot be re-validated. Validation results are stored all-or-nothing, so a failed run does not leave partial validation results.

---

## Exception Management

An exception represents an individual stored validation failure.

The exception management module provides:

- Filtering by batch, category, severity, rule, and review status.
- Case-insensitive partial record ID search.
- Sorting and pagination.
- Exception details containing the raw source record.
- Individual and bulk review operations.
- CSV report exports.
- Complete review history.

### Review statuses

| Status | Meaning |
|---|---|
| `OPEN` | Exception has not been reviewed |
| `IN_REVIEW` | Exception is being investigated |
| `REVIEWED` | Exception has been reviewed |
| `WAIVED` | Exception has been explicitly waived |

### Review controls

- Review history is append-only; each decision creates a new record.
- The current status is determined by the latest review, or `OPEN` if no review exists.
- A reviewer name is required.
- Waiving or reopening an exception requires a comment.
- Repeating the current status is refused unless a comment is provided.
- Concurrent reviewers cannot both successfully update the same exception.
- Bulk review supports up to 500 exceptions and uses all-or-nothing behaviour.
- Reviews and report exports are recorded in the audit log.

### CSV export safety

CSV exports include protection against formula injection. Text values beginning with `=`, `+`, `-`, or `@` are prefixed with an apostrophe, while plain numeric values such as `-5.00` are preserved.

Search treats `%` and `_` literally.

### Performance observations

In single-run development sandbox observations with 1,000,000 stored failures:

| Operation | Observed time |
|---|---:|
| First exception list page | Approximately 4 seconds |
| Page at offset 900,000 | Approximately 8 seconds |
| Full CSV export | Approximately 20 seconds |

These are development observations, not formal benchmarks. Typical project batches are substantially smaller.

---

## Audit Logging and Data Lineage

The audit and lineage module provides traceability across ingestion, validation, scoring, exception review, and report export operations.

### Audited actions

- `INGEST`
- `VALIDATE`
- `SCORE`
- `RULE_UPDATE`
- `EXCEPTION_REVIEW`
- `EXPORT_EXCEPTIONS`

Audit entries record information such as:

- Actor and timestamp.
- Related batch.
- Records processed.
- Number of failures.
- Processing duration.
- Rules executed.
- Operation outcome.

Audit entries store counts, hashes, and reasons rather than raw record contents.

Failed ingestions are recorded without a batch ID because no batch exists yet. They can be retrieved using `status=FAILED`.

### Batch lineage

Batch lineage provides:

- Original source filename.
- File hash, size, uploader, and upload time.
- Destination table and ingested record count.
- Processing event timeline.
- Validation failures and review progress.
- Quality score information.
- Source-file integrity verification.

The system re-hashes the original file and compares it with the SHA-256 hash recorded at ingestion.

Possible verification states:

- `verified`
- `hash_mismatch`
- `missing`

The system also checks whether the number of database records matches the ingested record count.

### Exception lineage

Exception lineage traces an individual validation failure back to:

1. Its original source file.
2. Its corresponding source row.
3. Its ingestion batch.
4. The database record stored by the application.

The source row is compared with the database record. A mismatch or missing source file is reported rather than silently repaired.

Pass `verify_file=false` to skip file verification checks.

### Append-only enforcement

PostgreSQL triggers defined in `db/schema.sql` prevent `UPDATE` and `DELETE` operations on:

- `audit_logs`
- `exception_reviews`

This provides database-level protection against accidental and application-level modification.

**Security limitation:** A database owner with sufficient privileges can deliberately remove or bypass these triggers. This mechanism is intended to protect against ordinary application-level tampering, not a fully privileged database administrator.

A batch with audit history cannot be deleted.

### Database upgrades

When upgrading an existing database from an earlier development step, execute the **Append-only enforcement** section at the end of `db/schema.sql`, or recreate the database using:

```bash
docker compose down -v
docker compose --env-file .env up -d db
```

**Warning:** The `-v` option removes the associated Docker volumes, including persisted database data. Use it only when you are comfortable resetting the database.

---

## Testing

DataGuard uses Pytest and HTTPX for automated testing.

The test suite includes coverage for:

- Dataset ingestion.
- CSV parsing and profiling.
- Validation rule execution.
- Data quality calculations.
- Exception filtering and review workflows.
- Bulk review transactions.
- Audit logging.
- Source-file integrity verification.
- Batch and exception lineage.
- Append-only database enforcement.
- Error handling and edge cases.

Run the test suite using:

```bash
pytest -v
```

### Test database configuration

Integration tests require a dedicated PostgreSQL test database.

**Windows (PowerShell):**

```powershell
$env:TEST_DATABASE_URL="postgresql+psycopg://dataguard:change_me@localhost:5432/dataguard_test"
pytest -v
```

**Linux / macOS:**

```bash
export TEST_DATABASE_URL="postgresql+psycopg://dataguard:change_me@localhost:5432/dataguard_test"
pytest -v
```

**Important database warning:** Integration tests drop and recreate the `public` schema of the database specified by `TEST_DATABASE_URL`.

Always use a dedicated test database. Never point this variable to a working or production database.

---

## Known Limitations

- If the server is terminated during validation, a batch can remain in the `VALIDATING` state until manually reset.
- Audit and review append-only enforcement protects against application-level changes, but not deliberate actions by a privileged database owner.
- The Streamlit dashboard and deployment workflow are not yet completed.
- Performance observations are from a development sandbox and are not formal production benchmarks.

---

## Development Roadmap

| Component | Status |
|---|---|
| Foundation and database setup | Complete |
| Synthetic sample data generation | Complete |
| Dataset ingestion | Complete |
| Configurable validation engine | Complete |
| Automated quality scoring | Complete |
| Exception management | Complete |
| Audit logging and data lineage | Complete |
| Streamlit dashboard | Planned |
| Deployment | Planned |
| Cloud integration (GCP) | Future scope |

### Planned enhancements

- Interactive Streamlit dashboard with Plotly visualizations.
- Dashboard views for ingestion, quality trends, exceptions, and audit activity.
- Deployment and production configuration.
- Potential integration with Google Cloud Storage, BigQuery, and IAM.

---

## Project Notes

- All sample financial data is synthetic.
- Actual runtime uploads and environment secrets should not be committed to the public repository.
- The database schema is maintained in `db/schema.sql`.
- API endpoint documentation is available through FastAPI's Swagger UI when the application is running.

---

**DataGuard — Making Data Quality Measurable, Traceable, and Governable.**
