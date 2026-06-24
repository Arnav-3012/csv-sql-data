import logging
import uuid
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.types import Date, DateTime

from db.connection import get_engine

logger = logging.getLogger(__name__)


def insert_data(df, table_name, username, filename,
                skip_duplicates=False, truncated=False):
    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    logger.info(
        "Starting insert | batch_id=%s | table=%s | user=%s", batch_id, table_name, username
    )

    df = df.copy()

    if skip_duplicates:
        before = len(df)
        df = df.drop_duplicates()
        skipped = before - len(df)
        if skipped:
            logger.info("Skipping %d duplicate rows before insert", skipped)

    rows_json = df.to_json(orient="records", date_format="iso")

    try:
        engine = get_engine()
        with engine.begin() as conn:
            inspector = sa_inspect(engine)
            table_cols = inspector.get_columns(table_name)
            for col in table_cols:
                col_name = col["name"]
                if col_name in df.columns:
                    if isinstance(col["type"], (Date, DateTime)):
                        df[col_name] = pd.to_datetime(
                            df[col_name],
                            dayfirst=True,
                            errors="coerce"
                        ).dt.strftime("%Y-%m-%d %H:%M:%S")

            df.to_sql(table_name, con=conn, if_exists="append", index=False, method="multi")

            conn.execute(
                text(
                    "INSERT INTO upload_log "
                    "(batch_id, uploaded_by, target_table, file_name, rows_inserted, uploaded_at, rolled_back, notes) "
                    "VALUES (:batch_id, :uploaded_by, :target_table, :file_name, :rows_inserted, NOW(), FALSE, :notes)"
                ),
                {
                    "batch_id": batch_id,
                    "uploaded_by": username,
                    "target_table": table_name,
                    "file_name": filename,
                    "rows_inserted": len(df),
                    "notes": rows_json + ("|TRUNCATED" if truncated else ""),
                },
            )

        logger.info(
            "Insert complete | %d rows | batch_id=%s | table=%s", len(df), batch_id, table_name
        )
        return {
            "success": True,
            "batch_id": batch_id,
            "rows_inserted": len(df),
            "timestamp": now_iso,
            "error": None,
        }

    except Exception as e:
        logger.error("Insert failed | batch_id=%s | error=%s", batch_id, e)
        return {
            "success": False,
            "batch_id": batch_id,
            "rows_inserted": 0,
            "timestamp": now_iso,
            "error": str(e),
        }
