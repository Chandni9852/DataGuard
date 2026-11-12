"""Module 1: CSV ingestion.

Flow: check upload -> decode -> parse everything as text -> normalise headers -> check required
columns -> create batch -> bulk load -> audit. Everything that touches the database runs in a
single transaction, so a failed ingestion leaves no partial batch behind; the failure itself is
then recorded in the audit log.

Raw-first: every value is stored exactly as it appeared in the file. Only an entirely empty
cell becomes NULL. Whitespace-only cells are kept as-is because they are evidence for the
completeness rule. Nothing is trimmed, coerced or dropped.
"""
import csv
import io
import logging
import re
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.errors import DataGuardError
from backend.models import Customer, Dataset, IngestionBatch, Transaction
from backend.services import audit
from backend.services.schema_detection import detect_schema
from backend.utils import file_checks
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

CHUNK_SIZE = 5000
PREVIEW_ROWS_DEFAULT = 20
_ACTOR_OK = re.compile(r"^[\w .@\-]{1,100}$")

# Per-dataset ingestion specs. Adding a dataset type means adding an entry here.
DATASET_SPECS: dict[str, dict] = {
    "transactions": {
        "required": ["transaction_id", "customer_id", "transaction_amount",
                     "transaction_date", "merchant", "transaction_status"],
        "optional": [],
        "table": "transactions",
        "validated": True,       # goes on to the validation engine
    },
    "customers": {
        "required": ["customer_id"],
        "optional": ["customer_name"],
        "table": "customers",
        "validated": False,      # reference data, loaded as-is
    },
}


# ---------------------------------------------------------------- parsing

def _normalise_header(h: str) -> str:
    return re.sub(r"[\s\-]+", "_", h.strip().lower())


def _read_header_and_check_rows(text: str) -> list[str]:
    """Return the raw header row. Every data record must have as many fields as the header;
    the first offender is reported by data-record number (blank lines are skipped)."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise DataGuardError("empty", "The file has no header row.")
    width, record = len(header), 0
    try:
        for row in reader:
            if not row:          # blank line, skipped by the loader too
                continue
            record += 1
            if len(row) != width:
                raise DataGuardError(
                    "malformed",
                    f"Data record {record} has {len(row)} fields but the header has {width}.",
                    record=record)
    except csv.Error as exc:
        raise DataGuardError("malformed", f"The file is not valid CSV: {exc}")
    return header


def parse_csv(data: bytes) -> tuple[pd.DataFrame, dict[str, str]]:
    """Return (all-text DataFrame with normalised headers, {original: normalised} for renamed ones).

    The header is validated from the raw CSV row, not from pandas, because pandas silently
    renames empty headers ("Unnamed: 1") and exact duplicates ("a", "a.1"), which would hide
    problems such as a duplicated required column.
    """
    text = file_checks.decode_csv_bytes(data)
    original = _read_header_and_check_rows(text)
    normalised = [_normalise_header(c) for c in original]
    if any(c == "" for c in normalised):
        raise DataGuardError("malformed", "The header row contains an empty column name.")
    dupes = sorted({c for c in normalised if normalised.count(c) > 1})
    if dupes:
        raise DataGuardError("malformed", f"Duplicate column names in header: {', '.join(dupes)}.")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_filter=False,
                         header=0, names=normalised)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataGuardError("malformed", f"The file could not be parsed as CSV: {str(exc)[:200]}")
    renamed = {o: n for o, n in zip(original, normalised) if o != n}
    return df, renamed


def _column_report(df: pd.DataFrame, spec: dict) -> tuple[list[str], list[str], list[str]]:
    cols = list(df.columns)
    missing = [c for c in spec["required"] if c not in cols]
    known = set(spec["required"]) | set(spec["optional"])
    unexpected = [c for c in cols if c not in known]
    warnings: list[str] = []
    if unexpected:
        warnings.append(f"Ignoring unexpected columns: {', '.join(unexpected)}.")
    return missing, unexpected, warnings


def _missing_columns_message(df: pd.DataFrame, missing: list[str]) -> str:
    msg = f"Missing required columns: {', '.join(missing)}."
    if len(df.columns) == 1 and re.search(r"[;\t|]", str(df.columns[0])):
        msg += " The file may use a delimiter other than a comma."
    return msg


# ---------------------------------------------------------------- datasets

def list_datasets(session: Session) -> list[Dataset]:
    return list(session.scalars(select(Dataset).order_by(Dataset.dataset_id)))


def get_dataset(session: Session, dataset_id: int) -> Dataset:
    ds = session.get(Dataset, dataset_id)
    if ds is None:
        raise DataGuardError("not_found", f"Dataset {dataset_id} does not exist.")
    if ds.dataset_name not in DATASET_SPECS:
        raise DataGuardError("bad_request", f"Dataset '{ds.dataset_name}' has no ingestion specification.")
    return ds


# ---------------------------------------------------------------- preview

def preview(session: Session, dataset_id: int, filename: str | None, content_type: str | None,
            data: bytes, rows: int = PREVIEW_ROWS_DEFAULT) -> dict:
    """Parse and profile an upload without persisting anything."""
    ds = get_dataset(session, dataset_id)
    spec = DATASET_SPECS[ds.dataset_name]
    file_checks.check_upload_metadata(filename, content_type)
    file_checks.check_size(len(data), get_settings().max_upload_bytes)
    df, renamed = parse_csv(data)
    missing, unexpected, warnings = _column_report(df, spec)
    if renamed:
        warnings.append("Column names were normalised (trimmed, lower-cased, spaces to underscores).")
    if len(df) == 0:
        warnings.append("The file contains a header but no data rows.")
    return {
        "dataset_name": ds.dataset_name,
        "file_name": file_checks.sanitize_filename(filename),
        "size_bytes": len(data),
        "sha256": file_checks.sha256_hex(data),
        "total_rows": len(df),
        "schema_valid": not missing,
        "required_columns": spec["required"],
        "missing_required_columns": missing,
        "unexpected_columns": unexpected,
        "renamed_columns": renamed,
        "columns": detect_schema(df),
        "preview_rows": df.head(max(0, min(rows, 100))).to_dict("records"),
        "warnings": warnings,
    }


# ---------------------------------------------------------------- ingestion

def _clean_actor(actor: str | None) -> str:
    actor = (actor or "system").strip()
    if not _ACTOR_OK.match(actor):
        raise DataGuardError("bad_request", "uploaded_by may only contain letters, digits, spaces and . _ @ -")
    return actor


def _records_none_if_empty(df: pd.DataFrame) -> list[dict]:
    """Rows as dicts; an entirely empty cell becomes None (NULL), everything else is kept verbatim."""
    return [{k: (None if v == "" else v) for k, v in rec.items()} for rec in df.to_dict("records")]


def _load_transactions(session: Session, df: pd.DataFrame, batch_id: uuid.UUID) -> int:
    cols = DATASET_SPECS["transactions"]["required"]
    records = _records_none_if_empty(df[cols])
    for i, rec in enumerate(records, start=1):
        rec["batch_id"] = batch_id
        rec["source_row_number"] = i
    for start in range(0, len(records), CHUNK_SIZE):
        session.execute(insert(Transaction), records[start:start + CHUNK_SIZE])
    return len(records)


def _load_customers(session: Session, df: pd.DataFrame) -> tuple[int, list[str]]:
    warnings: list[str] = []
    ids = df["customer_id"].str.strip()
    blank = ids == ""
    if blank.any():
        first = int(blank.idxmax()) + 1
        raise DataGuardError(
            "malformed",
            f"{int(blank.sum())} customer rows have a blank customer_id (first at data record {first}). "
            "Reference data must be keyed.")
    names = df["customer_name"] if "customer_name" in df.columns else pd.Series([""] * len(df))
    frame = pd.DataFrame({"customer_id": ids, "customer_name": names})
    dupes = int(frame.duplicated("customer_id", keep="last").sum())
    if dupes:
        warnings.append(f"{dupes} duplicate customer_id rows in the file; the last occurrence was kept.")
        frame = frame.drop_duplicates("customer_id", keep="last")
    records = _records_none_if_empty(frame)
    for start in range(0, len(records), CHUNK_SIZE):
        stmt = pg_insert(Customer).values(records[start:start + CHUNK_SIZE])
        session.execute(stmt.on_conflict_do_update(
            index_elements=["customer_id"], set_={"customer_name": stmt.excluded.customer_name}))
    return len(records), warnings


def ingest(session: Session, dataset_id: int, filename: str | None, content_type: str | None,
           data: bytes, uploaded_by: str | None = None, force: bool = False) -> tuple[IngestionBatch, list[str]]:
    """Ingest one CSV upload as a new batch. Commits on success; rolls back and audits on failure."""
    settings = get_settings()
    ds = get_dataset(session, dataset_id)
    spec = DATASET_SPECS[ds.dataset_name]
    safe_name = file_checks.sanitize_filename(filename)
    sha = file_checks.sha256_hex(data)
    batch_id = uuid.uuid4()
    stored_path: Path | None = None
    actor = "system"
    try:
        actor = _clean_actor(uploaded_by)
        file_checks.check_upload_metadata(filename, content_type)
        file_checks.check_size(len(data), settings.max_upload_bytes)
        df, _ = parse_csv(data)
        missing, _, warnings = _column_report(df, spec)
        if missing:
            raise DataGuardError("missing_columns", _missing_columns_message(df, missing), missing=missing)

        if not force:
            existing = session.scalar(
                select(IngestionBatch.batch_id)
                .where(IngestionBatch.dataset_id == ds.dataset_id, IngestionBatch.file_sha256 == sha)
                .order_by(IngestionBatch.ingestion_timestamp.desc()).limit(1))
            if existing:
                raise DataGuardError(
                    "duplicate_file",
                    f"An identical file was already ingested as batch {existing}. Pass force=true to ingest it again.",
                    existing_batch_id=str(existing))
        if len(df) == 0:
            warnings.append("The file contains a header but no data rows; the batch has 0 records.")

        # Keep the original bytes under a generated name (lineage + future cloud storage).
        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored_path = upload_dir / f"{batch_id}.csv"
        stored_path.write_bytes(data)

        batch = IngestionBatch(
            batch_id=batch_id, dataset_id=ds.dataset_id, source_file=safe_name,
            file_name=stored_path.name, file_size_bytes=len(data), file_sha256=sha,
            ingestion_timestamp=utcnow(), total_records=0, processing_status="UPLOADED",
            uploaded_by=actor, destination_table=spec["table"])
        session.add(batch)
        session.flush()

        if ds.dataset_name == "transactions":
            loaded = _load_transactions(session, df, batch_id)
        else:
            loaded, extra = _load_customers(session, df)
            warnings.extend(extra)
        batch.total_records = loaded
        if not spec["validated"]:        # reference data has no validation stage
            batch.processing_status = "COMPLETED"
            batch.completed_at = utcnow()

        audit.log_action(session, "INGEST", "SUCCESS", batch_id=batch_id, actor=actor,
                         records_processed=loaded,
                         details={"dataset": ds.dataset_name, "source_file": safe_name,
                                  "sha256": sha, "size_bytes": len(data), "forced": force})
        session.commit()
        logger.info("Ingested batch %s (%s rows) into %s", batch_id, loaded, spec["table"])
        return batch, warnings

    except DataGuardError as exc:
        _fail(session, stored_path, ds.dataset_name, safe_name, sha, actor, exc.kind, exc.message)
        raise
    except Exception:
        _fail(session, stored_path, ds.dataset_name, safe_name, sha, actor, "internal_error",
              "Unexpected error during ingestion")
        logger.exception("Ingestion failed for dataset %s", ds.dataset_name)
        raise


def _fail(session: Session, stored_path: Path | None, dataset: str, source_file: str,
          sha: str, actor: str, kind: str, reason: str) -> None:
    session.rollback()
    if stored_path is not None:
        stored_path.unlink(missing_ok=True)
    audit.log_action(session, "INGEST", "FAILED", actor=actor,
                     details={"dataset": dataset, "source_file": source_file,
                              "sha256": sha, "kind": kind, "reason": reason})
    session.commit()


# ---------------------------------------------------------------- queries

def _batch_query():
    return (select(IngestionBatch, Dataset.dataset_name)
            .join(Dataset, Dataset.dataset_id == IngestionBatch.dataset_id))


def list_batches(session: Session, dataset_id: int | None, limit: int, offset: int) -> list[tuple]:
    q = _batch_query().order_by(IngestionBatch.ingestion_timestamp.desc()).limit(limit).offset(offset)
    if dataset_id is not None:
        q = q.where(IngestionBatch.dataset_id == dataset_id)
    return list(session.execute(q).all())


def count_batches(session: Session, dataset_id: int | None) -> int:
    q = select(func.count()).select_from(IngestionBatch)
    if dataset_id is not None:
        q = q.where(IngestionBatch.dataset_id == dataset_id)
    return session.scalar(q) or 0


def get_batch(session: Session, batch_id: uuid.UUID) -> tuple:
    row = session.execute(_batch_query().where(IngestionBatch.batch_id == batch_id)).first()
    if row is None:
        raise DataGuardError("not_found", f"Batch {batch_id} does not exist.")
    return row
