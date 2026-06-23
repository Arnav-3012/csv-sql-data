import logging

import pandas as pd
from sqlalchemy import inspect
from sqlalchemy.types import Integer, BigInteger, Float, Numeric, Date, DateTime, String, CHAR

logger = logging.getLogger(__name__)

from db.connection import get_engine

_AUTO_COLS = {"id", "batch_id"}

_EMPTY_RESULT = lambda errors, warnings: {
    "valid": False,
    "errors": errors,
    "warnings": warnings,
    "duplicate_indices": [],
    "auto_renamed_columns": {},
}


def get_table_columns(table_name):
    engine = get_engine()
    return [col["name"] for col in inspect(engine).get_columns(table_name)]


def validate(df, table_name):
    errors = []
    warnings = []
    duplicate_indices = []
    auto_renamed_columns = {}

    # 0. Duplicate column names — hard block before anything else
    duped_cols = df.columns[df.columns.duplicated()].tolist()
    if duped_cols:
        msg = (
            f"File has duplicate column names: {duped_cols}. "
            "Each column must appear exactly once. Fix the file before uploading."
        )
        logger.error(msg)
        return {
            "valid": False,
            "errors": [msg],
            "warnings": [],
            "duplicate_indices": [],
            "auto_renamed_columns": {},
        }

    # 1. Empty file check
    if len(df) == 0:
        errors.append("File is empty. No data to insert.")
        return _EMPTY_RESULT(errors, warnings)

    engine = get_engine()
    inspector = inspect(engine)
    table_cols = inspector.get_columns(table_name)
    table_col_map = {col["name"]: col for col in table_cols}
    table_col_lower = {name.lower(): name for name in table_col_map}

    # Case-insensitive auto-rename
    rename_map = {}
    for csv_col in df.columns:
        if csv_col not in table_col_map and csv_col.lower() in table_col_lower:
            correct = table_col_lower[csv_col.lower()]
            rename_map[csv_col] = correct
            auto_renamed_columns[csv_col] = correct
            warnings.append(f"Column '{csv_col}' auto-renamed to '{correct}' to match table schema.")
    if rename_map:
        df = df.rename(columns=rename_map)

    # Whitespace-only values → NaN
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].replace(r"^\s*$", pd.NA, regex=True)

    required_cols = {name for name in table_col_map if name not in _AUTO_COLS}
    csv_cols = set(df.columns)

    # 2. Column match check
    unknown = csv_cols - set(table_col_map.keys())
    if unknown:
        errors.append(f"Unknown columns not in table: {sorted(unknown)}")

    missing = required_cols - csv_cols
    if missing:
        errors.append(f"Required table columns missing from file: {sorted(missing)}")

    column_check_passed = not unknown and not missing

    # 3. All-null column warning
    for col in df.columns:
        if col in _AUTO_COLS:
            continue
        if df[col].isnull().all():
            warnings.append(f"Column '{col}' has no values — all rows are empty for this column.")

    # 4. Null check on NOT NULL columns
    for col in table_cols:
        name = col["name"]
        if name in _AUTO_COLS or name not in csv_cols:
            continue
        if not col.get("nullable", True):
            null_mask = df[name].isnull()
            n = null_mask.sum()
            if n:
                bad_rows = df.index[null_mask].tolist()
                errors.append(f"Column '{name}' has {n} null value(s) in rows: {bad_rows}")

    # 5. Type coercion check (with DATE auto-conversion)
    for col in table_cols:
        name = col["name"]
        if name in _AUTO_COLS or name not in csv_cols:
            continue
        col_type = col["type"]
        series = df[name].dropna()
        if isinstance(col_type, (Integer, BigInteger, Float, Numeric)):
            bad = pd.to_numeric(series, errors="coerce").isnull()
            bad_rows = series.index[bad].tolist()
            if bad_rows:
                errors.append(f"Column '{name}' has non-numeric values in rows: {bad_rows}")
        elif isinstance(col_type, (Date, DateTime)):
            converted = pd.to_datetime(series, errors="coerce")
            bad_rows = series.index[converted.isnull()].tolist()
            if bad_rows:
                errors.append(f"Column '{name}' has invalid date/datetime values in rows: {bad_rows}")
            else:
                not_iso = not series.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}").all()
                if not_iso:
                    df[name] = pd.to_datetime(df[name], errors="coerce").dt.strftime("%Y-%m-%d")
                    warnings.append(f"Column '{name}' date format auto-converted to YYYY-MM-DD.")

    # 6. VARCHAR/CHAR length check
    for col in table_cols:
        name = col["name"]
        if name in _AUTO_COLS or name not in csv_cols:
            continue
        col_type = col["type"]
        if isinstance(col_type, (String, CHAR)):
            max_len = getattr(col_type, "length", None)
            if max_len is None:
                continue
            series = df[name].dropna().astype(str)
            if series.str.len().max() > max_len:
                bad_rows = df[df[name].notna() & (df[name].astype(str).str.len() > max_len)].index.tolist()
                errors.append(
                    f"Column '{name}' has values exceeding max length of "
                    f"{max_len} characters in rows: {bad_rows}"
                )
                logger.error(
                    "Column '%s' exceeds max length %d in rows: %s", name, max_len, bad_rows
                )

    # 8. Row count warning
    if len(df) > 5000:
        warnings.append(f"Large file: {len(df)} rows detected. Please confirm before inserting.")

    # 9. Duplicate PK check
    pk_info = inspector.get_pk_constraint(table_name)
    pk_cols = [c for c in pk_info.get("constrained_columns", []) if c not in _AUTO_COLS]
    checkable_pk = [c for c in pk_cols if c in csv_cols]
    if checkable_pk:
        dupes = df[df.duplicated(subset=checkable_pk, keep=False)]
        if not dupes.empty:
            dupe_vals = dupes[checkable_pk].drop_duplicates().values.tolist()
            errors.append(
                f"Duplicate primary key values found in column(s) {checkable_pk}: {dupe_vals}"
            )

    # 10. Fully duplicate rows within CSV
    full_dupe_mask = df.duplicated(keep="first")
    if full_dupe_mask.any():
        n = int(full_dupe_mask.sum())
        duplicate_indices = df.index[full_dupe_mask].tolist()
        warnings.append(
            f"Found {n} fully duplicate rows in file at row indices: {duplicate_indices}. "
            "These will be skipped on insert."
        )

    # 11. Rows already present in DB (only if column check passed)
    if column_check_passed:
        check_cols = [c for c in csv_cols if c not in _AUTO_COLS]
        try:
            existing_df = pd.read_sql(
                f"SELECT {', '.join(check_cols)} FROM {table_name}",
                con=engine,
            )
            if not existing_df.empty:
                merged = df[check_cols].merge(existing_df, on=check_cols, how="inner")
                if not merged.empty:
                    n = len(merged)
                    sample = merged.head(3).to_dict(orient="records")
                    errors.append(
                        f"Found {n} rows already present in the database. "
                        f"Remove them before inserting. Conflicting values: {sample}"
                    )
        except Exception:
            pass

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "duplicate_indices": duplicate_indices,
        "auto_renamed_columns": auto_renamed_columns,
    }
