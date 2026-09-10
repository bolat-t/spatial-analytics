"""One DuckDB connection, with spatial loaded and the raw files registered."""
from __future__ import annotations

import duckdb

from .config import DB_PATH, RAW, ensure_dirs

FIRE_PARQUET = RAW / "npws_fire_history.parquet"


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    ensure_dirs()
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def register_raw(con: duckdb.DuckDBPyConnection) -> None:
    """Views over the raw files, so nothing is copied before it needs to be."""
    if not FIRE_PARQUET.exists():
        raise SystemExit(
            f"{FIRE_PARQUET.name} missing — run: "
            "uv run python -m spatial_analytics.ingest_fire"
        )
    con.execute(
        f"create or replace view fire_raw as "
        f"select * from read_parquet('{FIRE_PARQUET.as_posix()}')"
    )
