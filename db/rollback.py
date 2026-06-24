import json
import logging

import pandas as pd
from sqlalchemy import text

from db.connection import get_engine

logger = logging.getLogger(__name__)


def rollback_batch(batch_id, table_name):
    logger.info("Rollback started | batch_id=%s | table=%s", batch_id, table_name)

    try:
        engine = get_engine()
        with engine.begin() as conn:
            log_row = conn.execute(
                text("SELECT rolled_back, notes FROM upload_log WHERE batch_id = :batch_id"),
                {"batch_id": batch_id},
            ).fetchone()

            if log_row is None or log_row._mapping["rolled_back"]:
                return {
                    "success": False,
                    "rows_deleted": 0,
                    "error": f"Batch {batch_id} has already been rolled back.",
                }

            log_row = conn.execute(
                text("SELECT notes FROM upload_log WHERE batch_id = :bid"),
                {"bid": batch_id}
            ).fetchone()

            if log_row and log_row[0] and "|TRUNCATED" in str(log_row[0]):
                return {
                    "success": False,
                    "rows_deleted": 0,
                    "error": (
                        "This batch involved a table truncate. "
                        "The inserted rows can be deleted but "
                        "pre-truncate data cannot be recovered."
                    )
                }

            notes = log_row._mapping["notes"]
            rows = json.loads(notes)
            df_to_delete = pd.DataFrame(rows)

            rows_deleted = 0
            for _, row in df_to_delete.iterrows():
                row_dict = row.to_dict()
                conditions = " AND ".join([f"{col} = :{col}" for col in df_to_delete.columns])
                result = conn.execute(
                    text(f"DELETE FROM {table_name} WHERE {conditions} LIMIT 1"),
                    row_dict,
                )
                rows_deleted += result.rowcount

            conn.execute(
                text(
                    "UPDATE upload_log SET rolled_back = 1, rolled_back_at = NOW() "
                    "WHERE batch_id = :batch_id"
                ),
                {"batch_id": batch_id},
            )

        logger.info(
            "Rollback complete | %d rows deleted | batch_id=%s", rows_deleted, batch_id
        )
        return {"success": True, "rows_deleted": rows_deleted, "error": None}

    except Exception as e:
        logger.error("Rollback failed | batch_id=%s | error=%s", batch_id, e)
        return {"success": False, "rows_deleted": 0, "error": str(e)}


def get_upload_history(username=None):
    try:
        engine = get_engine()
        with engine.connect() as conn:
            if username:
                rows = conn.execute(
                    text(
                        "SELECT * FROM upload_log WHERE uploaded_by = :username "
                        "ORDER BY uploaded_at DESC"
                    ),
                    {"username": username},
                ).mappings().all()
            else:
                rows = conn.execute(
                    text("SELECT * FROM upload_log ORDER BY uploaded_at DESC")
                ).mappings().all()

        return [dict(row) for row in rows]

    except Exception as e:
        logger.error("Failed to fetch upload history | error=%s", e)
        return []
