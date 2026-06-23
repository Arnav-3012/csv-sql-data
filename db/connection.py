import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def _require_env(key):
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def get_engine():
    host = _require_env("DB_HOST")
    port = _require_env("DB_PORT")
    name = _require_env("DB_NAME")
    user = _require_env("DB_USER")
    password = _require_env("DB_PASSWORD")

    url = f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{name}"
    return create_engine(url, connect_args={"connect_timeout": 10}, pool_pre_ping=True)


def get_connection():
    return get_engine().connect()


if __name__ == "__main__":
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("DB connection successful")
