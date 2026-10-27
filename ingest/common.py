"""Shared building blocks for both loaders: paths, the DuckDB connection, the load log,
idempotent partition replacement, HTTP retries and run summaries.

Every loader follows the same contract:
  * the response or file is saved untouched in the raw landing zone before it is parsed;
  * the parsed rows replace one partition in a single transaction (delete, insert, log),
    so reruns never duplicate rows and a failure never leaves a half-loaded partition;
  * every partition attempt is written to raw.ingest_log with its status, so --resume
    can skip what is already loaded and retry what failed.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import requests

DEFAULT_DB_PATH = Path("data") / "corn.duckdb"
DEFAULT_RAW_DIR = Path("data") / "raw"
RETRY_STATUSES = {429, 500, 502, 503, 504}
USER_AGENT = "us-corn-market-monitor/0.1 (+https://github.com/JosueFC/us-corn-market-monitor)"

DDL = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SEQUENCE IF NOT EXISTS raw.ingest_log_seq;
CREATE TABLE IF NOT EXISTS raw.ingest_log (
    log_id        BIGINT DEFAULT nextval('raw.ingest_log_seq'),
    source        VARCHAR NOT NULL,   -- 'nass' or 'wasde'
    partition_key VARCHAR NOT NULL,   -- e.g. 'price_received_national/year=2024'
    status        VARCHAR NOT NULL,   -- ok | failed | no_data
    n_rows        BIGINT,
    fingerprint   VARCHAR,            -- sha256 of the loaded content
    error         VARCHAR,
    _logged_at    TIMESTAMP DEFAULT now()
);
"""


class IngestError(RuntimeError):
    """Base class for errors raised by the loaders."""


class SchemaDrift(IngestError):
    """The source no longer has the shape the loader expects. Affects every partition, so runs stop."""


class MalformedFile(IngestError):
    """A file could be read but its content is broken (truncated rows, empty file, bad encoding)."""


def db_path() -> Path:
    return Path(os.environ.get("CORN_DB", DEFAULT_DB_PATH))


def raw_dir() -> Path:
    return Path(os.environ.get("CORN_RAW_DIR", DEFAULT_RAW_DIR))


def connect(path: Path | str | None = None, extra_ddl: str = "") -> duckdb.DuckDBPyConnection:
    """Open (and create if needed) the DuckDB database with the raw schema and the load log."""
    path = Path(path) if path is not None else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(DDL)
    if extra_ddl:
        con.execute(extra_ddl)
    return con


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact(text: str, secret: str | None) -> str:
    """Remove a secret (the API key) from text before it is printed or written to the log."""
    if secret:
        text = text.replace(secret, "***")
    return text


def log_status(con, source: str, partition_key: str, status: str, n_rows: int = 0,
               fingerprint: str | None = None, error: str | None = None) -> None:
    con.execute(
        "INSERT INTO raw.ingest_log (source, partition_key, status, n_rows, fingerprint, error) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [source, partition_key, status, n_rows, fingerprint, error],
    )


def latest_status(con, source: str, partition_key: str) -> tuple[str, str | None] | None:
    """Return (status, fingerprint) of the most recent attempt at a partition, or None if never tried."""
    return con.execute(
        "SELECT status, fingerprint FROM raw.ingest_log WHERE source = ? AND partition_key = ? "
        "ORDER BY log_id DESC LIMIT 1",
        [source, partition_key],
    ).fetchone()


def is_loaded(con, source: str, partition_key: str, fingerprint: str | None = None) -> bool:
    """True when the latest attempt succeeded (and, if given, loaded exactly this content)."""
    last = latest_status(con, source, partition_key)
    if last is None or last[0] != "ok":
        return False
    return fingerprint is None or last[1] == fingerprint


def replace_partition(con, table: str, where: dict[str, object], df: pd.DataFrame, source: str,
                      partition_key: str, fingerprint: str | None = None) -> int:
    """Idempotent load: delete the partition and insert the new rows in ONE transaction, then log it.

    `where` holds the partition columns and their values; they are also written to every row.
    Columns are matched by name, so the frame may omit nullable columns.
    """
    df = df.copy()
    for col, val in where.items():
        df[col] = val
    clause = " AND ".join(f"{col} = ?" for col in where)
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(f"DELETE FROM {table} WHERE {clause}", list(where.values()))
        if len(df):
            con.register("_partition_df", df)
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _partition_df")
            con.unregister("_partition_df")
        log_status(con, source, partition_key, "ok", len(df), fingerprint)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(df)


def get_with_retries(session, url: str, params: dict | None = None, retries: int = 5,
                     timeout: tuple[float, float] = (15, 120), label: str = "",
                     secret: str | None = None) -> requests.Response:
    """GET with exponential backoff on timeouts, dropped connections and 429/5xx responses.

    Any other status is returned to the caller, which knows how to interpret it.
    """
    last_problem = "no attempt made"
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_problem = redact(f"{type(e).__name__}: {e}", secret)
        else:
            if resp.status_code not in RETRY_STATUSES:
                return resp
            last_problem = f"HTTP {resp.status_code}"
        wait = 2 * 2 ** attempt
        print(f"  {label}: {last_problem}, retry {attempt + 1}/{retries} in {wait}s")
        time.sleep(wait)
    raise IngestError(f"{label}: gave up after {retries} attempts ({last_problem})")


@dataclass
class RunResult:
    """What a run did. A non-empty `failures` list makes the command exit with code 1."""
    rows: int = 0
    loaded: int = 0
    skipped: int = 0
    no_data: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)   # (partition_key, error)


def report(result: RunResult, source: str) -> int:
    """Print a run summary and return the process exit code."""
    print(f"\n{source}: {result.loaded} partition(s) loaded ({result.rows} rows), "
          f"{result.skipped} skipped as already loaded, {len(result.no_data)} with no data, "
          f"{len(result.failures)} failed")
    for key, err in result.failures:
        print(f"  FAILED {key}: {err}")
    if result.failures:
        print("Fix the cause if needed, then rerun the same command with --resume to retry only what is missing.")
    return 1 if result.failures else 0
