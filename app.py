import logging
import os
from pathlib import Path

import streamlit as st
from sqlalchemy import text

from db.connection import get_engine
from db.rollback import get_upload_history, rollback_batch
from db.uploader import insert_data
from db.validators import validate
from utils.file_parser import parse_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="CSV → MySQL Uploader", layout="wide")

# ─── Sidebar ──────────────────────────────────────────────────────────────────

_LOGO_CANDIDATES = ["assets/logo.png", "assets/logo.jpg", "assets/logo.jpeg"]
_logo_path = next((Path(p) for p in _LOGO_CANDIDATES if Path(p).exists()), None)

if _logo_path:
    st.sidebar.image(str(_logo_path), use_column_width=True)
else:
    st.sidebar.markdown(
        "<div style='width:100%; padding:12px; background:#2a2a2a; "
        "border-radius:8px; text-align:center; color:#666; font-size:12px;'>"
        "📁 Drop logo.png in /assets</div>",
        unsafe_allow_html=True,
    )
st.sidebar.markdown("##### CSV → MySQL Uploader")
st.sidebar.divider()

username = st.sidebar.text_input(
    "👤 Enter Your Name",
    value=st.session_state.get("username", ""),
    placeholder="e.g. Arnav Shah",
)
st.sidebar.caption("Your name will be logged with every upload.")
st.session_state.username = username

try:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    st.sidebar.success("🟢 DB Connected")
except Exception as e:
    st.sidebar.error("🔴 DB Connection Failed")
    logger.error("DB connection check failed | error=%s", e)

if not username.strip():
    st.sidebar.warning("Please enter your name to continue.")
    st.stop()

# ─── Main Area Header ─────────────────────────────────────────────────────────

st.markdown("## 📤 CSV → MySQL Data Uploader")
st.markdown(
    "Upload structured data directly into the database "
    "with validation, audit logging, and rollback support."
)
st.divider()

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab1, tab2 = st.tabs(["Upload Data", "History & Rollback"])

# ─── Tab 1: Upload Data ───────────────────────────────────────────────────────

with tab1:
    # Table selector
    st.markdown("#### 🗄️ Step 1 — Select Target Table")
    try:
        with get_engine().connect() as conn:
            raw_tables = conn.execute(text("SHOW TABLES")).fetchall()
        all_tables = [row[0] for row in raw_tables if row[0] != "upload_log"]
        allowed = os.getenv("ALLOWED_TABLES", "")
        allowed_list = [t.strip() for t in allowed.split(",") if t.strip()]
        if allowed_list:
            all_tables = [t for t in all_tables if t in allowed_list]
        if not all_tables:
            st.error(
                "No tables available. Check ALLOWED_TABLES in your .env file."
            )
            st.stop()
    except Exception as e:
        st.error(f"Failed to fetch tables: {e}")
        logger.error("Failed to fetch table list | error=%s", e)
        all_tables = []

    table_name = st.selectbox("Select Target Table", all_tables)

    # File uploader
    step2_col, reset_col = st.columns([8, 1])
    step2_col.markdown("#### 📁 Step 2 — Upload Your File")
    if reset_col.button("🔄 Reset", help="Clear the current file"):
        st.session_state.uploader_key = st.session_state.get("uploader_key", 0) + 1
        st.session_state.pop("validation_result", None)
        st.session_state.pop("df", None)
        st.rerun()

    uploaded_file = st.file_uploader(
        "Upload CSV or Excel",
        type=["csv", "xlsx", "xls"],
        key=f"file_uploader_{st.session_state.get('uploader_key', 0)}",
    )

    df = None
    if uploaded_file:
        try:
            df, parse_warnings = parse_file(uploaded_file)
            for pw in parse_warnings:
                st.warning(pw)
            st.session_state.df = df
            st.session_state.uploaded_filename = uploaded_file.name
            st.info(f"Preview: {len(df)} rows, {len(df.columns)} columns")
            with st.expander("👀 Preview uploaded data (first 10 rows)", expanded=True):
                st.dataframe(df.head(10), use_container_width=True)
        except Exception as e:
            st.error(f"File parse error: {e}")
            logger.error("File parse error | file=%s | error=%s", uploaded_file.name, e)
            df = None

    # Validation
    st.markdown("#### ✅ Step 3 — Validate & Insert")
    if df is not None:
        if st.button("Validate"):
            logger.info("Validation started | table=%s | user=%s", table_name, username)
            result = validate(df, table_name)
            st.session_state.validation_result = result
            if "df" in result:
                st.session_state.df = result["df"]

        if "validation_result" in st.session_state:
            vr = st.session_state.validation_result
            errs = vr["errors"]
            warns = vr["warnings"]
            dup_indices = vr.get("duplicate_indices", [])

            st.markdown(
                f"**Validation Summary:** {len(errs)} error(s), "
                f"{len(warns)} warning(s), {len(dup_indices)} duplicate row(s)"
            )

            if errs:
                with st.expander("❌ Hard Errors — fix before inserting", expanded=True):
                    for err in errs:
                        st.error(err)

            if warns:
                with st.expander("⚠️ Warnings — review before inserting", expanded=True):
                    for warn in warns:
                        st.warning(warn)

            if dup_indices:
                st.warning(f"{len(dup_indices)} duplicate rows found in file.")
                dcol1, dcol2 = st.columns(2)
                if dcol1.button("⛔ Block — I'll fix the file"):
                    st.session_state.skip_duplicates = False
                if dcol2.button("✅ Skip duplicates and insert unique rows"):
                    st.session_state.skip_duplicates = True

            if vr["valid"]:
                st.success("✅ All checks passed. Ready to insert.")

            st.divider()
            st.markdown("#### 🗑️ Optional — Truncate Table Before Insert")
            with st.expander("⚠️ What does truncate do?", expanded=False):
                st.warning(
                    "Truncating permanently deletes ALL existing rows in the "
                    "selected table BEFORE inserting your new data. "
                    "Pre-truncate data cannot be recovered or rolled back. "
                    "Use only when you want to fully replace table contents."
                )
            # FIX: read value from session state so checkbox persists across reruns
            truncate_before_insert = st.checkbox(
                "🗑️ Truncate table before inserting (deletes ALL existing rows)",
                value=st.session_state.get("truncate_before_insert", False),
                key="truncate_checkbox",
            )
            if truncate_before_insert:
                st.error(
                    f"⚠️ ALL existing rows in '{table_name}' will be deleted "
                    f"before insert. This cannot be undone."
                )
            st.session_state.truncate_before_insert = truncate_before_insert

    # Insert
    vr = st.session_state.get("validation_result")
    if st.session_state.get("df") is not None and vr and vr["valid"]:
        if st.button("Insert Data"):
            logger.info(
                "Insert started | table=%s | user=%s | file=%s",
                table_name,
                username,
                st.session_state.get("uploaded_filename", "unknown.csv"),
            )

            # Truncate if requested
            if st.session_state.get("truncate_before_insert", False):
                try:
                    with get_engine().begin() as conn:
                        conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                    st.success(f"✅ Table '{table_name}' truncated successfully. All existing rows deleted.")
                    logger.info("Table truncated | table=%s | user=%s", table_name, username)
                except Exception as e:
                    st.error(f"Truncate failed: {e}")
                    logger.error("Truncate failed | table=%s | error=%s", table_name, e)
                    st.stop()

            result = insert_data(
                st.session_state.df,
                table_name,
                username,
                st.session_state.get("uploaded_filename", "unknown.csv"),
                skip_duplicates=st.session_state.get("skip_duplicates", False),
                truncated=st.session_state.get("truncate_before_insert", False),
            )

            if result["success"]:
                msg = (
                    f"✅ {result['rows_inserted']} rows inserted into "
                    f"'{table_name}' by {username} at {result['timestamp']}"
                )
                st.success(msg)
                if st.session_state.get("truncate_before_insert", False):
                    st.info("ℹ️ Table was truncated before this insert.")
                logger.info(msg)
                st.session_state.last_batch_id = result["batch_id"]
                st.session_state.last_upload_table = table_name
                st.session_state.truncate_before_insert = False

                col1, col2 = st.columns(2)
                if col1.button("📤 Upload Another File"):
                    st.session_state.pop("validation_result", None)
                    st.session_state.pop("df", None)
                    st.session_state.uploader_key = st.session_state.get("uploader_key", 0) + 1
                    st.rerun()
                if col2.button("📋 View History"):
                    st.session_state.active_tab = "history"
                    st.rerun()
            else:
                st.error(f"Insert failed: {result['error']}")

# ─── Tab 2: History & Rollback ────────────────────────────────────────────────

with tab2:
    # Quick rollback
    st.markdown("#### ↩️ Quick Rollback")
    last_batch_id = st.session_state.get("last_batch_id")
    last_table = st.session_state.get("last_upload_table")

    if last_batch_id:
        st.info(f"Last upload: batch {last_batch_id} into {last_table}")
        if st.button("↩ Rollback Last Upload"):
            logger.info(
                "Quick rollback started | batch_id=%s | table=%s",
                last_batch_id,
                last_table,
            )
            result = rollback_batch(last_batch_id, last_table)
            if result["success"]:
                st.success(
                    f"✅ Rolled back {result['rows_deleted']} rows from batch {last_batch_id}"
                )
                del st.session_state.last_batch_id
            else:
                st.error(result["error"])

    st.divider()

    # Full history table
    st.markdown("#### 📋 Full Upload History")
    history = get_upload_history(username)

    if not history:
        st.info("No upload history found for this user.")
    else:
        import pandas as pd

        history_df = pd.DataFrame(history)[
            [
                "batch_id",
                "target_table",
                "file_name",
                "rows_inserted",
                "uploaded_at",
                "rolled_back",
                "rolled_back_at",
            ]
        ]
        st.dataframe(history_df, use_container_width=True)

        active_batches = [r for r in history if not r["rolled_back"]]
        if active_batches:
            active_ids = [r["batch_id"] for r in active_batches]
            selected_batch_id = st.selectbox("Select a batch to rollback", active_ids)
            selected_record = next(
                r for r in active_batches if r["batch_id"] == selected_batch_id
            )

            if st.button("↩ Rollback Selected Batch"):
                logger.info(
                    "History rollback started | batch_id=%s | table=%s",
                    selected_batch_id,
                    selected_record["target_table"],
                )
                result = rollback_batch(
                    selected_batch_id, selected_record["target_table"]
                )
                if result["success"]:
                    st.success(
                        f"✅ Rolled back {result['rows_deleted']} rows from batch {selected_batch_id}"
                    )
                    st.rerun()
                else:
                    st.error(result["error"])

st.divider()
st.caption("Internal tool — all uploads are logged and auditable.")
