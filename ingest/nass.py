"""Load corn statistics from the USDA NASS Quick Stats API into raw.nass_quickstats.

Usage (PowerShell; the same commands work in bash):
    $env:NASS_API_KEY = "<your key>"            # bash: export NASS_API_KEY=<your key>
    python -m ingest.nass --check-config        # verify every configured value against the API
    python -m ingest.nass                       # load every query, every year from its start_year
    python -m ingest.nass --resume              # skip partitions already loaded; refresh recent years
    python -m ingest.nass --query price_received_national --start-year 2020

Design:
  * One partition = one configured query x one year. A partition is replaced in a single transaction,
    so a rerun or a revision never duplicates rows.
  * Every response is saved untouched under data/raw/nass/<query>/year=<year>/<pulled_at>.json before
    it is parsed. Quick Stats only serves the latest revision of each number, so these timestamped
    snapshots are the only record of what the API returned on a given day.
  * The API returns at most 50,000 rows per request. A response at that size is rejected as possibly
    truncated instead of being loaded.
  * The API key is read from NASS_API_KEY and removed from every message and log entry.
  * Field values are kept as text exactly as served (for example "1,234,000" or "(D)"); typing and
    the handling of suppression codes happen in the dbt staging models.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

from ingest import common
from ingest.common import IngestError, RunResult, SchemaDrift

SOURCE = "nass"
PROG = "python -m ingest.nass"
BASE_URL = "https://quickstats.nass.usda.gov/api"
ROW_LIMIT = 50_000
PAUSE_SECONDS = 2.0          # pause between requests; the API sits behind a gateway that blocks bursts
BLOCK_WAITS = (30, 90, 180)  # seconds to wait before retrying after the gateway blocks a request
DEFAULT_CONFIG = Path("config") / "nass_queries.yml"
RESERVED_PARAMS = {"key", "format", "year"}

# API field name -> raw column name. Required fields must be present in every record.
REQUIRED_FIELDS = {
    "source_desc": "source_desc",
    "commodity_desc": "commodity_desc",
    "class_desc": "class_desc",
    "prodn_practice_desc": "prodn_practice_desc",
    "util_practice_desc": "util_practice_desc",
    "statisticcat_desc": "statisticcat_desc",
    "unit_desc": "unit_desc",
    "short_desc": "short_desc",
    "domain_desc": "domain_desc",
    "domaincat_desc": "domaincat_desc",
    "agg_level_desc": "agg_level_desc",
    "state_alpha": "state_alpha",
    "state_name": "state_name",
    "state_fips_code": "state_fips_code",
    "year": "year",
    "freq_desc": "freq_desc",
    "begin_code": "begin_code",
    "end_code": "end_code",
    "reference_period_desc": "reference_period_desc",
    "Value": "value",
}
# Useful but not essential; stored as NULL if the API stops sending them.
OPTIONAL_FIELDS = {
    "group_desc": "group_desc",
    "location_desc": "location_desc",
    "week_ending": "week_ending",
    "load_time": "load_time",
    "CV (%)": "cv_pct",
}
FIELD_MAP = {**REQUIRED_FIELDS, **OPTIONAL_FIELDS}

DDL = "CREATE TABLE IF NOT EXISTS raw.nass_quickstats (\n" \
      "    query_name     VARCHAR NOT NULL,  -- partition: configured query\n" \
      "    partition_year INTEGER NOT NULL,  -- partition: year requested\n" + \
      "".join(f"    {col} VARCHAR,\n" for col in FIELD_MAP.values()) + \
      "    _pulled_at     TIMESTAMP NOT NULL, -- when the API was queried (UTC)\n" \
      "    _ingested_at   TIMESTAMP DEFAULT now()\n);"


class NoData(IngestError):
    """The query returned no rows for this year (for example, a year not yet published)."""


class TooManyRows(IngestError):
    """The query hit the API's 50,000-row limit; it must be narrowed before it can be loaded."""


class GatewayBlocked(IngestError):
    """The gateway in front of the API refused the request (an HTML 403/429 page rather than a JSON error)."""


class AuthError(IngestError):
    """The API rejected the key. Affects every request, so runs stop."""


# --------------------------------------------------------------------------- configuration

def load_queries(path: Path | str = DEFAULT_CONFIG) -> dict[str, dict]:
    """Read and validate the query configuration."""
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    queries = cfg.get("queries")
    if not isinstance(queries, dict) or not queries:
        raise ValueError(f"{path}: expected a non-empty 'queries' mapping")
    for name, q in queries.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)):
            raise ValueError(f"Query name {name!r} must be lower_snake_case")
        params = q.get("params")
        if not isinstance(params, dict) or not params:
            raise ValueError(f"Query {name}: 'params' must be a non-empty mapping")
        reserved = RESERVED_PARAMS & set(params)
        if reserved:
            raise ValueError(f"Query {name}: {sorted(reserved)} are set by the loader, not the config")
        for k, v in params.items():
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"Query {name}: parameter {k} must be a non-empty string")
        start = q.get("start_year")
        if not isinstance(start, int) or not 1866 <= start <= dt.date.today().year:
            raise ValueError(f"Query {name}: start_year must be a year between 1866 and this year")
    return queries


# --------------------------------------------------------------------------- API calls

def _api_key() -> str:
    key = os.environ.get("NASS_API_KEY", "").strip()
    if key.startswith("<") or key.endswith(">"):
        sys.exit("NASS_API_KEY still holds a placeholder (" + key + "). Set it to the key from your NASS email.")
    if not key:
        sys.exit("NASS_API_KEY is not set. PowerShell: $env:NASS_API_KEY = \"<key>\"   "
                 "bash: export NASS_API_KEY=<key>   (free key: https://quickstats.nass.usda.gov/api/)")
    return key


def _error_text(resp, api_key: str) -> str:
    """The API's error message, without the key (some errors echo the request back)."""
    try:
        body = resp.json()
        msg = "; ".join(map(str, body.get("error", []))) or json.dumps(body)
    except (ValueError, AttributeError):
        msg = resp.text
    return common.redact(msg, api_key)[:300]


def _is_gateway_block(resp) -> bool:
    """A 403/429 whose body is not the API's JSON error format comes from the gateway, not the API."""
    return resp.status_code in (403, 429) and not resp.text.lstrip().startswith("{")


def _call(endpoint: str, params: dict, api_key: str, session, label: str) -> dict:
    """Call an endpoint and return its JSON body, translating API errors into typed exceptions."""
    full = {**params, "key": api_key, "format": "JSON"}
    for wait in (*BLOCK_WAITS, None):
        resp = common.get_with_retries(session, f"{BASE_URL}/{endpoint}/", full, label=label, secret=api_key)
        if not _is_gateway_block(resp):
            break
        if wait is None:
            raise GatewayBlocked(
                f"{label}: the gateway in front of the API kept refusing requests (HTTP {resp.status_code}, "
                "an HTML page, not a JSON error, so this is not a key problem). Wait 10-15 minutes, then "
                "rerun the same command with --resume.")
        print(f"  {label}: blocked by the gateway (HTTP {resp.status_code}), waiting {wait}s")
        time.sleep(wait)
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as e:
            raise SchemaDrift(f"{label}: response is not JSON: {_error_text(resp, api_key)}") from e
    msg = _error_text(resp, api_key)
    if resp.status_code in (401, 403) or "api key" in msg.lower() or "unauthorized" in msg.lower():
        raise AuthError(f"{label}: the API rejected the key (HTTP {resp.status_code}): {msg}")
    if resp.status_code == 413 or "limit" in msg.lower():
        raise TooManyRows(f"{label}: more than {ROW_LIMIT:,} rows; narrow the query ({msg})")
    if resp.status_code == 400:
        # Quick Stats answers HTTP 400 both for a query with no rows and for an invalid parameter.
        # --check-config tells the two apart before a load; during a load it is recorded as no data.
        raise NoData(f"{label}: no rows ({msg})")
    raise IngestError(f"{label}: HTTP {resp.status_code}: {msg}")


def fetch(params: dict, year: int, api_key: str, session=None, label: str = "") -> list[dict]:
    """Return the records for one query and one year."""
    session = session or requests.Session()
    body = _call("api_GET", {**params, "year": str(year)}, api_key, session, label)
    if "data" not in body or not isinstance(body["data"], list):
        raise SchemaDrift(f"{label}: response has no 'data' list (keys: {sorted(body)[:10]})")
    records = body["data"]
    if not records:
        raise NoData(f"{label}: empty data list")
    if len(records) >= ROW_LIMIT:
        raise TooManyRows(f"{label}: {len(records):,} rows returned, which may be truncated at the "
                          f"{ROW_LIMIT:,}-row limit; narrow the query")
    return records


def get_counts(params: dict, api_key: str, session=None, label: str = "") -> int:
    body = _call("get_counts", params, api_key, session or requests.Session(), label)
    try:
        return int(body["count"])
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaDrift(f"{label}: get_counts returned {body!r}") from e


def get_param_values(param: str, api_key: str, session=None) -> list[str]:
    body = _call("get_param_values", {"param": param}, api_key, session or requests.Session(), param)
    values = body.get(param)
    if not isinstance(values, list):
        raise SchemaDrift(f"get_param_values({param}) returned keys {sorted(body)[:10]}")
    return [str(v) for v in values]


# --------------------------------------------------------------------------- parsing and loading

def to_frame(records: list[dict], label: str = "") -> pd.DataFrame:
    """Check the schema of every record and keep the mapped fields as text."""
    for i, rec in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in rec]
        if missing:
            raise SchemaDrift(f"{label}: record {i} is missing required fields {missing}; "
                              f"fields present: {sorted(rec)[:12]}")
    rows = [{col: (None if rec.get(api) is None else str(rec.get(api))) for api, col in FIELD_MAP.items()}
            for rec in records]
    return pd.DataFrame(rows, columns=list(FIELD_MAP.values()), dtype=object)


def save_raw(query: str, year: int, params: dict, pulled_at: dt.datetime, records: list[dict]) -> Path:
    """Write the response to the landing zone. The request is stored without the key."""
    stamp = pulled_at.strftime("%Y%m%dT%H%M%SZ")
    path = common.raw_dir() / "nass" / query / f"year={year}" / f"{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"request": {**params, "year": str(year)}, "pulled_at": pulled_at.isoformat(), "data": records}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def load_partition(con, query: str, year: int, records: list[dict], pulled_at: dt.datetime) -> int:
    df = to_frame(records, f"{query}/{year}")
    df["_pulled_at"] = pulled_at.replace(tzinfo=None)
    fingerprint = common.sha256(json.dumps(records, sort_keys=True).encode("utf-8"))
    return common.replace_partition(
        con, "raw.nass_quickstats", {"query_name": query, "partition_year": year}, df,
        SOURCE, partition_key(query, year), fingerprint)


def partition_key(query: str, year: int) -> str:
    return f"{query}/year={year}"


def connect(path=None):
    return common.connect(path, DDL)


# --------------------------------------------------------------------------- runs

def run(queries: dict[str, dict], years_by_query: dict[str, list[int]], api_key: str, con,
        resume: bool = False, refresh_years: int = 2, session=None) -> RunResult:
    """Load every (query, year) partition.

    resume=True skips partitions whose last load succeeded, except the newest `refresh_years`
    years, which NASS still revises. A failed partition is logged and the run continues; schema
    drift, a rejected key and a gateway block affect every request, so they are logged and stop the run.
    A query for which every requested year comes back empty is reported as a failure, because
    that pattern means a wrong filter rather than unpublished data.
    """
    result = RunResult()
    session = session or requests.Session()
    this_year = dt.date.today().year
    for query, years in years_by_query.items():
        params = queries[query]["params"]
        attempted = empty_years = 0
        for year in years:
            key = partition_key(query, year)
            if resume and year <= this_year - refresh_years and common.is_loaded(con, SOURCE, key):
                result.skipped += 1
                continue
            attempted += 1
            try:
                pulled_at = dt.datetime.now(dt.UTC)
                records = fetch(params, year, api_key, session, label=key)
                save_raw(query, year, params, pulled_at, records)
                n = load_partition(con, query, year, records, pulled_at)
            except NoData as e:
                common.log_status(con, SOURCE, key, "no_data", error=str(e))
                result.no_data.append(key)
                empty_years += 1
                continue
            except (SchemaDrift, AuthError, GatewayBlocked) as e:
                msg = f"{type(e).__name__}: {e}"
                common.log_status(con, SOURCE, key, "failed", error=msg)
                result.failures.append((key, msg))
                raise
            except Exception as e:  # noqa: BLE001 - log everything and keep going
                msg = common.redact(f"{type(e).__name__}: {e}", api_key)[:500]
                print(f"FAILED {key}: {msg}")
                common.log_status(con, SOURCE, key, "failed", error=msg)
                result.failures.append((key, msg))
                continue
            finally:
                time.sleep(PAUSE_SECONDS)
            result.rows += n
            result.loaded += 1
            print(f"loaded {key}: {n} rows")
        if attempted > 1 and empty_years == attempted:
            result.failures.append((query, "every requested year returned no rows; check the filters "
                                           "with --check-config"))
    return result


def check_config(queries: dict[str, dict], api_key: str, probe_year: int, session=None) -> int:
    """Verify every configured value against get_param_values, and every query against get_counts.

    Prints a report and returns 1 if anything is wrong, so it can gate a load in scripts and CI.
    """
    session = session or requests.Session()
    problems = 0
    wanted: dict[str, set[str]] = {}
    for q in queries.values():
        for param, value in q["params"].items():
            wanted.setdefault(param, set()).add(value)

    print("Parameter values (get_param_values):")
    for param in sorted(wanted):
        allowed = set(get_param_values(param, api_key, session))
        for value in sorted(wanted[param]):
            if value in allowed:
                print(f"  ok       {param} = {value}")
            else:
                problems += 1
                close = difflib.get_close_matches(value, sorted(allowed), n=3, cutoff=0.6)
                hint = f" (closest: {', '.join(close)})" if close else ""
                print(f"  UNKNOWN  {param} = {value}{hint}")
        time.sleep(PAUSE_SECONDS)

    print(f"\nRows per query for {probe_year} (get_counts):")
    for name, q in queries.items():
        try:
            n = get_counts({**q["params"], "year": str(probe_year)}, api_key, session, name)
        except NoData:
            n = 0
        flag = "ok   " if 0 < n < ROW_LIMIT else "ERROR"
        if flag == "ERROR":
            problems += 1
        print(f"  {flag} {name:28s} {n:>8,}")
        time.sleep(PAUSE_SECONDS)

    print("\nConfiguration OK." if not problems else f"\n{problems} problem(s) found; fix config/nass_queries.yml.")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--check-config", action="store_true", help="verify the configuration against the API and exit")
    ap.add_argument("--query", action="append", help="load only this query (repeatable)")
    ap.add_argument("--start-year", type=int, help="override each query's start_year")
    ap.add_argument("--end-year", type=int, help="last year to load (default: this year)")
    ap.add_argument("--resume", action="store_true", help="skip partitions already loaded")
    ap.add_argument("--refresh-years", type=int, default=2,
                    help="with --resume, always reload this many most recent years (default 2)")
    args = ap.parse_args(argv)

    queries = load_queries(args.config)
    api_key = _api_key()
    end_year = args.end_year or dt.date.today().year

    if args.check_config:
        try:
            return check_config(queries, api_key, probe_year=end_year - 1)
        except (SchemaDrift, AuthError, IngestError) as e:
            print(f"STOPPED: {e}")
            return 2

    selected = args.query or list(queries)
    unknown = [q for q in selected if q not in queries]
    if unknown:
        sys.exit(f"Unknown query {unknown}; configured: {list(queries)}")
    years_by_query = {
        q: list(range(args.start_year or queries[q]["start_year"], end_year + 1)) for q in selected
    }
    con = connect()
    try:
        result = run(queries, years_by_query, api_key, con, resume=args.resume, refresh_years=args.refresh_years)
    except (SchemaDrift, AuthError, GatewayBlocked) as e:
        print(f"STOPPED: {e}")
        return 2
    return common.report(result, SOURCE)


if __name__ == "__main__":
    raise SystemExit(main())
