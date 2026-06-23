import logging
from io import BytesIO

import pandas as pd

logger = logging.getLogger(__name__)


def parse_file(uploaded_file):
    filename = uploaded_file.name
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    logger.info("Parsing file: %s", filename)

    parse_warnings = []

    try:
        data = BytesIO(uploaded_file.read())

        if ext == "csv":
            try:
                df = pd.read_csv(data, encoding="utf-8")
            except UnicodeDecodeError:
                data.seek(0)
                df = pd.read_csv(data, encoding="latin-1")
                msg = f"File '{filename}' encoding detected as latin-1, auto-converted to UTF-8."
                logger.warning(msg)
                parse_warnings.append(msg)

        elif ext in ("xlsx", "xls"):
            engine = "openpyxl" if ext == "xlsx" else "xlrd"
            xl = pd.ExcelFile(data, engine=engine)
            if len(xl.sheet_names) > 1:
                msg = (
                    f"Excel file has {len(xl.sheet_names)} sheets "
                    f"({', '.join(xl.sheet_names)}). "
                    f"Reading first sheet '{xl.sheet_names[0]}' only."
                )
                logger.warning(msg)
                parse_warnings.append(msg)
            df = pd.read_excel(xl, sheet_name=xl.sheet_names[0], engine=engine)

        else:
            logger.error("Unsupported file type: %s", filename)
            raise ValueError("Unsupported file type. Only .csv, .xlsx, .xls allowed.")

        df.columns = df.columns.str.strip()
        df = df.apply(lambda col: col.str.strip() if col.dtype == object else col)
        df = df.replace("", pd.NA)
        df = df.dropna(how="all")

        logger.info("Parsed %d rows, %d columns from %s", len(df), len(df.columns), filename)
        return df, parse_warnings

    except ValueError:
        raise
    except Exception as e:
        logger.error("Failed to parse %s: %s", filename, e)
        raise ValueError(f"Failed to parse {filename}: {e}")
