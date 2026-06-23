# CSV → MySQL Uploader

> Internal Streamlit tool for uploading CSV and Excel files directly into MySQL tables — with validation, audit logging, and one-click rollback.

---

## What This Does

Upload structured data files into any MySQL table through a browser UI. Every upload is validated against the live table schema before insert, logged with a unique batch ID, and can be rolled back row-by-row without touching the target table schema.

---

## Prerequisites

- Python 3.9+
- A running MySQL instance (local or remote)
- The `upload_log` audit table created in your database (see Setup)

---

## Quick Start

```bash
git clone <repo-url>
cd csv-uploader

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env            # then fill in your DB credentials
```

Create the audit table (one-time):

```sql
-- Run setup/init_audit_table.sql against your database
mysql -u root -p csv_upload_demo < setup/init_audit_table.sql
```

Start the app:

```bash
streamlit run app.py
```

---

## Configuration

Copy `.env.example` to `.env` and set your credentials:

```env
DB_HOST=localhost
DB_PORT=3306
DB_NAME=csv_upload_demo
DB_USER=root
DB_PASSWORD=yourpassword
```

To switch between local and production, only the `.env` file changes — no code edits needed.

---

## How to Use

**1. Enter your name** in the sidebar — it's logged with every upload (honor system, no password).

**2. Upload tab:**

| Step | Action |
|------|--------|
| 1 | Select the target MySQL table from the dropdown |
| 2 | Upload a `.csv`, `.xlsx`, or `.xls` file |
| 3 | Click **Validate** — review errors and warnings |
| 4 | Click **Insert Data** once all checks pass |

**3. History & Rollback tab:**

- **Quick Rollback** — one click to undo the last upload in your current session
- **Full History** — table of all your past uploads; select any batch and roll it back

---

## Validation Checks (in order)

1. File extension must be `.csv`, `.xlsx`, or `.xls`
2. File must not be empty
3. Duplicate column names are blocked
4. All CSV columns must exist in the target table (no unknown columns)
5. All required (`NOT NULL`) columns must be present
6. No nulls in `NOT NULL` columns
7. Type compatibility — int, float, date, varchar
8. VARCHAR/CHAR length limits enforced
9. Row count warning if > 5000 rows
10. Duplicate primary key check
11. Fully duplicate rows within the file (offered as skip, not hard block)
12. Rows already present in the database are blocked

Column names are matched **case-insensitively** and auto-renamed to match the table schema with a warning.

---

## Rollback

Rollback deletes inserted rows by matching all column values exactly (`LIMIT 1` per row — safe for tables with duplicates). The target table schema is never modified. After rollback, the batch is marked in `upload_log` and cannot be rolled back again.

---

## Project Structure

```
csv-uploader/
├── app.py                      # Streamlit UI (Upload + History tabs)
├── db/
│   ├── connection.py           # SQLAlchemy engine, reads from .env
│   ├── validators.py           # All validation logic
│   ├── uploader.py             # Insert + audit log write
│   └── rollback.py             # Row-level delete + log update
├── utils/
│   └── file_parser.py          # CSV/Excel → pandas DataFrame
├── setup/
│   └── init_audit_table.sql    # One-time audit table creation
├── assets/                     # Place logo.png here for sidebar branding
├── .env.example                # Environment variable template
└── requirements.txt
```

---

## Audit Log Schema (`upload_log`)

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Auto-increment PK |
| `batch_id` | uuid | Unique ID per upload session |
| `uploaded_by` | varchar | Free-text username from sidebar |
| `target_table` | varchar | MySQL table rows were inserted into |
| `file_name` | varchar | Original file name |
| `rows_inserted` | int | Number of rows written |
| `uploaded_at` | datetime | Timestamp of insert |
| `rolled_back` | bool | Whether the batch has been rolled back |
| `rolled_back_at` | datetime | Timestamp of rollback (null if not rolled back) |
| `notes` | longtext | Inserted rows stored as JSON (used for rollback) |

---

## Running Tests

```bash
python -m pytest test_validators.py test_uploader.py test_rollback.py test_file_parser.py -v
```

---

## Branding

Drop a `logo.png` (or `.jpg`/`.jpeg`) into the `assets/` folder — it will appear automatically in the sidebar.
