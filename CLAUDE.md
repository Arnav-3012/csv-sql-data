# CSV/Excel → MySQL Upload Tool

## Project Purpose
Internal Streamlit tool for uploading CSV/Excel files directly into MySQL tables.
Supports validation, audit logging, and batch rollback.

## Tech Stack
- UI: Streamlit
- File parsing: pandas, openpyxl, xlrd
- DB: MySQL via SQLAlchemy + pymysql driver
- Config: python-dotenv (.env file)
- Auth: free-text username (honor system, stored in audit log)

## Folder Structure
- app.py               → main Streamlit UI (tabs: Upload, History/Rollback)
- db/connection.py     → SQLAlchemy engine factory, reads from .env
- db/validators.py     → all validation logic (schema, nulls, types, row count)
- db/uploader.py       → insert logic, batch_id generation, wraps in transaction
- db/rollback.py       → delete by batch_id, mark rolled_back in upload_log
- utils/file_parser.py → CSV/Excel → pandas DataFrame
- setup/init_audit_table.sql → one-time SQL to create upload_log table
- assets/              → logo image folder. Place logo.png here for sidebar branding.

## .env Variables
DB_HOST=localhost
DB_PORT=3306
DB_NAME=csv_upload_demo
DB_USER=root
DB_PASSWORD=yourpassword

## Database Rules
- Target tables are used READ-ONLY in terms of schema — no ALTER TABLE,
  no added columns, no modifications of any kind.
  batch_id lives only in upload_log as an internal tracking ID.
  Rollback works by storing inserted rows as JSON in upload_log.notes
  and deleting by exact row value match on rollback.
- upload_log table must be created before running (run setup/init_audit_table.sql)
- Table list is fetched live each run — no caching
- All inserts wrapped in a single transaction (full rollback if any row fails)

## upload_log Schema
id, batch_id (UUID), uploaded_by (free text), target_table, file_name,
rows_inserted, uploaded_at, rolled_back (bool), rolled_back_at, notes

## Validation Rules (in order)
1. File must be .csv, .xlsx, or .xls
2. File must not be empty
3. All CSV columns must exist in the target table (no unknown columns)
4. All required (NOT NULL) columns must be present in CSV
5. Null check — no nulls in NOT NULL columns
6. Type coercion check — int/float/date/varchar compatibility
7. Row count warning if > 5000 rows (soft warn, user confirms)
8. Duplicate PK check if primary key is detectable

## Validation uses SQLAlchemy inspector
Use `sqlalchemy.inspect(engine).get_columns(table_name)` for live schema.
Use `inspect(engine).get_pk_constraint(table_name)` for PK check.

## Streamlit Session State Keys
- st.session_state.username         → entered name, persists across tabs
- st.session_state.current_batch_id → UUID of current upload session
- st.session_state.last_upload_table → last used table name
- st.session_state.validation_result → result dict from validators.py

## Rollback Logic
- Quick rollback: deletes rows from last batch_id in session state
- History rollback: user picks any batch from upload_log, same delete logic
- Rollback reads the inserted rows from upload_log.notes (stored as JSON at insert time)
- Deletes by matching ALL column values exactly, LIMIT 1 per row (safe for tables with duplicates)
- No batch_id column dependency on target tables — target table schema is never touched
- After delete: UPDATE upload_log SET rolled_back=TRUE, rolled_back_at=NOW()
- Rollback only allowed if rolled_back=FALSE

## Streamlit UI Layout
Sidebar: username text input (persists in session_state)
Tab 1 - Upload:
  - Dropdown: select target table (fetched live)
  - File uploader: CSV or Excel
  - Validation results display
  - Preview: first 10 rows in st.dataframe
  - Confirm & Insert button (disabled until validation passes)
Tab 2 - History & Rollback:
  - Quick button: "Rollback last upload"
  - Full table: upload_log filtered by username
  - Per-row rollback button for each batch

## Demo vs Production Switch
Only the .env file changes. Zero code changes needed.
Demo: DB_HOST=localhost (DBeaver on Mac)
Prod: DB_HOST=<office_server_ip> (MySQL Workbench on office machine)

## Key Implementation Notes
- batch_id is uuid4() generated once per upload, injected into every row as extra column
- uploader.py uses engine.begin() context manager so transaction auto-rolls back on error
- Table dropdown calls SHOW TABLES every render (no st.cache)
- validators.py returns a dict: {valid: bool, errors: [], warnings: []}
- All SQL uses parameterized queries — no string interpolation

## Logging Requirements

Every Python file in this project must use Python's built-in logging module, not print statements.

Standard setup at the top of every file:
import logging
logger = logging.getLogger(__name__)

Log these events at the correct level:
- logger.info() for: engine created, validation started, file parsed, insert started, rollback started
- logger.warning() for: row count > 5000, nullable column has nulls, type coercion issues
- logger.error() for: DB connection failed, validation hard block triggered, insert failed, rollback failed
- logger.debug() for: individual row checks, column name comparisons

In app.py, configure the root logger at startup:
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
)

This ensures all terminal output is timestamped and traceable.
Do not use print() anywhere in the project except test scripts.