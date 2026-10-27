"""Load WASDE report data, exactly as each monthly report published it, into raw.wasde.

USDA's Office of the Chief Economist publishes the data behind every WASDE report since April 2010:
one CSV per report from 2021 on, and multi-year ZIP archives of CSVs for 2010-2020. The files
are not revised after publication, which makes them the point-in-time source for the US corn
balance sheet and USDA's projected season-average farm price.

Usage (PowerShell; the same commands work in bash):
    python -m ingest.wasde --download            # find every file on the USDA page, download, load
    python -m ingest.wasde --download --resume   # fetch only new files; load only new or changed content
    python -m ingest.wasde --from-dir data/manual/wasde   # load files downloaded in a browser
    python -m ingest.wasde --inventory           # list the corn attributes and units that were loaded

Design:
  * Downloads are saved untouched under data/raw/wasde/files/ before they are parsed.
  * One partition = one CSV (a monthly file, or one CSV inside a ZIP archive), replaced in a single
    transaction. Each load records the content's sha256, so --resume reloads a file only when
    USDA has changed it.
  * Some reports were re-issued with a "-V2" suffix in the file name. Both versions are loaded with
    their file names; the staging model chooses one version per report.
  * The header is checked against the columns the downstream models need; drift fails loudly.
    Every value is kept as text; typing happens in the dbt staging models.
  * The whole file is kept (every commodity), so the raw table is a faithful copy of the source.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from ingest import common
from ingest.common import IngestError, MalformedFile, RunResult, SchemaDrift

SOURCE = "wasde"
PROG = "python -m ingest.wasde"
PAGE_URL = os.environ.get("WASDE_PAGE_URL", "https://www.usda.gov/historical-wasde-report-data-3")
FILE_NAME_RE = re.compile(r"^oce-wasde-report-data-[0-9A-Za-z_-]+\.(csv|zip)$", re.IGNORECASE)

# Header verified against oce-wasde-report-data-2021-01.csv (October 2026).
REQUIRED_COLUMNS = {
    "WasdeNumber": "wasde_number",
    "ReportDate": "report_date",
    "ReportTitle": "report_title",
    "Attribute": "attribute",
    "Commodity": "commodity",
    "Region": "region",
    "MarketYear": "market_year",
    "ProjEstFlag": "proj_est_flag",
    "Value": "value",
    "Unit": "unit",
    "ReleaseDate": "release_date",
}
OPTIONAL_COLUMNS = {
    "ReliabilityProjection": "reliability_projection",
    "AnnualQuarterFlag": "annual_quarter_flag",
    "ReleaseTime": "release_time",
    "ForecastYear": "forecast_year",
    "ForecastMonth": "forecast_month",
}
COLUMN_MAP = {**REQUIRED_COLUMNS, **OPTIONAL_COLUMNS}

DDL = "CREATE TABLE IF NOT EXISTS raw.wasde (\n" \
      "    source_file  VARCHAR NOT NULL,  -- partition: file name as published\n" \
      "    member       VARCHAR NOT NULL,  -- partition: CSV inside a ZIP (= source_file for a CSV)\n" + \
      "".join(f"    {col} VARCHAR,\n" for col in COLUMN_MAP.values()) + \
      "    _ingested_at TIMESTAMP DEFAULT now()\n);"


class DownloadBlocked(IngestError):
    """The USDA site refused an automated download."""


# --------------------------------------------------------------------------- discovery and download

class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def discover_links(html: str, base_url: str = PAGE_URL) -> list[str]:
    """Return the absolute URLs of every WASDE data file linked from the page, de-duplicated by file name."""
    parser = _LinkParser()
    parser.feed(html)
    by_name: dict[str, str] = {}
    for href in parser.hrefs:
        url = urljoin(base_url, href.strip())
        name = Path(urlparse(url).path).name
        if FILE_NAME_RE.match(name):
            by_name.setdefault(name, url)
    return [by_name[name] for name in sorted(by_name)]


def _blocked_message(what: str, status: int) -> str:
    return (f"The USDA site refused {what} (HTTP {status}). Download the files in a browser from "
            f"{PAGE_URL} into data/manual/wasde/ and run:  python -m ingest.wasde --from-dir data/manual/wasde")


def fetch_page(session=None) -> str:
    resp = common.get_with_retries(session or requests.Session(), PAGE_URL, label="WASDE page")
    if resp.status_code in (401, 403):
        raise DownloadBlocked(_blocked_message("the page request", resp.status_code))
    if resp.status_code != 200:
        raise IngestError(f"WASDE page: HTTP {resp.status_code}")
    return resp.text


def download(url: str, dest_dir: Path, session=None) -> Path:
    """Save one file untouched in the landing zone and return its path."""
    name = Path(urlparse(url).path).name
    resp = common.get_with_retries(session or requests.Session(), url, label=name, timeout=(15, 300))
    if resp.status_code in (401, 403):
        raise DownloadBlocked(_blocked_message(f"the download of {name}", resp.status_code))
    if resp.status_code != 200:
        raise IngestError(f"{name}: HTTP {resp.status_code}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    path.write_bytes(resp.content)
    return path


# --------------------------------------------------------------------------- parsing

def iter_csv_members(path: Path):
    """Yield (member name, bytes) for a CSV file, or for every CSV inside a ZIP archive."""
    if path.suffix.lower() == ".csv":
        yield path.name, path.read_bytes()
        return
    if path.suffix.lower() != ".zip":
        raise MalformedFile(f"{path.name}: expected a .csv or .zip file")
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist()
                     if n.lower().endswith(".csv") and not n.startswith("__MACOSX/")]
            if not names:
                raise MalformedFile(f"{path.name}: the archive contains no CSV files")
            for name in sorted(names):
                yield name, zf.read(name)
    except zipfile.BadZipFile as e:
        raise MalformedFile(f"{path.name}: not a valid ZIP archive ({e})") from e


def _decode(data: bytes, label: str) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise MalformedFile(f"{label}: neither UTF-8 nor Windows-1252 text")


def parse_csv(data: bytes, label: str) -> pd.DataFrame:
    """Parse one WASDE CSV into text columns, checking the header and the shape of every row."""
    reader = csv.reader(io.StringIO(_decode(data, label), newline=""))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration as e:
        raise MalformedFile(f"{label}: empty file") from e
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise SchemaDrift(f"{label}: missing required columns {missing}; header is {header}")
    idx = {col: header.index(api) for api, col in COLUMN_MAP.items() if api in header}
    rows = []
    try:
        for line_no, row in enumerate(reader, start=2):
            if not row:                      # blank line, e.g. at the end of the file
                continue
            if len(row) != len(header):
                raise MalformedFile(f"{label}: line {line_no} has {len(row)} fields, header has "
                                    f"{len(header)} (truncated or corrupted file)")
            rows.append({col: row[i] for col, i in idx.items()})
    except csv.Error as e:
        raise MalformedFile(f"{label}: unreadable CSV ({e})") from e
    if not rows:
        raise MalformedFile(f"{label}: header only, no data rows")
    return pd.DataFrame(rows, columns=list(COLUMN_MAP.values()), dtype=object)


# --------------------------------------------------------------------------- loading and runs

def connect(path=None):
    return common.connect(path, DDL)


def partition_key(source_file: str, member: str) -> str:
    return source_file if member == source_file else f"{source_file}::{member}"


def load_member(con, source_file: str, member: str, data: bytes) -> int:
    key = partition_key(source_file, member)
    df = parse_csv(data, key)
    return common.replace_partition(con, "raw.wasde", {"source_file": source_file, "member": member}, df,
                                    SOURCE, key, common.sha256(data))


def run(files: list[Path], con, resume: bool = False) -> RunResult:
    """Load every CSV in the given files. A broken file is logged and skipped; schema drift stops the run."""
    result = RunResult()
    for path in files:
        try:
            members = list(iter_csv_members(path))
        except MalformedFile as e:
            common.log_status(con, SOURCE, path.name, "failed", error=str(e))
            result.failures.append((path.name, str(e)))
            continue
        for member, data in members:
            key = partition_key(path.name, member)
            if resume and common.is_loaded(con, SOURCE, key, common.sha256(data)):
                result.skipped += 1
                continue
            try:
                n = load_member(con, path.name, member, data)
            except SchemaDrift as e:
                common.log_status(con, SOURCE, key, "failed", error=f"SchemaDrift: {e}")
                result.failures.append((key, f"SchemaDrift: {e}"))
                raise
            except Exception as e:  # noqa: BLE001 - log everything and keep going
                msg = f"{type(e).__name__}: {e}"[:500]
                print(f"FAILED {key}: {msg}")
                common.log_status(con, SOURCE, key, "failed", error=msg)
                result.failures.append((key, msg))
                continue
            result.rows += n
            result.loaded += 1
            print(f"loaded {key}: {n} rows")
    return result


def download_all(session=None, resume: bool = False) -> tuple[list[Path], list[tuple[str, str]]]:
    """Download every linked file. With resume, files already in the landing zone are not fetched again."""
    session = session or requests.Session()
    dest = common.raw_dir() / "wasde" / "files"
    urls = discover_links(fetch_page(session))
    if not urls:
        raise SchemaDrift(f"No WASDE data links found on {PAGE_URL}; the page layout may have changed")
    print(f"{len(urls)} files linked from {PAGE_URL}")
    paths, failures = [], []
    for url in urls:
        name = Path(urlparse(url).path).name
        if resume and (dest / name).exists():
            paths.append(dest / name)
            continue
        try:
            paths.append(download(url, dest, session))
            print(f"downloaded {name}")
        except DownloadBlocked:
            raise
        except Exception as e:  # noqa: BLE001
            failures.append((name, f"{type(e).__name__}: {e}"[:500]))
    return paths, failures


def inventory(con, commodity: str = "%corn%", region: str = "United States") -> pd.DataFrame:
    """Distinct report titles, attributes and units for a commodity and region, with the reports they span."""
    return con.execute(
        """
        SELECT commodity, report_title, attribute, unit,
               count(DISTINCT wasde_number) AS n_reports,
               min(try_cast(wasde_number AS INTEGER)) AS first_wasde_number,
               max(try_cast(wasde_number AS INTEGER)) AS last_wasde_number
        FROM raw.wasde
        WHERE commodity ILIKE ? AND region = ?
        GROUP BY ALL
        ORDER BY commodity, report_title, attribute, unit
        """,
        [commodity, region],
    ).df()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--download", action="store_true", help="download every file from the USDA page and load it")
    mode.add_argument("--from-dir", type=Path, help="load the .csv and .zip files in this folder")
    mode.add_argument("--inventory", action="store_true", help="list loaded corn attributes and units")
    ap.add_argument("--resume", action="store_true", help="skip files already loaded with identical content")
    args = ap.parse_args(argv)

    con = connect()
    if args.inventory:
        with pd.option_context("display.max_rows", None, "display.width", 200, "display.max_colwidth", 60):
            print(inventory(con).to_string(index=False))
        return 0

    download_failures: list[tuple[str, str]] = []
    if args.download:
        try:
            files, download_failures = download_all(resume=args.resume)
        except DownloadBlocked as e:
            print(e)
            return 2
    else:
        if not args.from_dir.is_dir():
            sys.exit(f"Folder not found: {args.from_dir}")
        files = sorted(p for p in args.from_dir.iterdir() if p.suffix.lower() in (".csv", ".zip"))
        if not files:
            sys.exit(f"No .csv or .zip files in {args.from_dir}")
    try:
        result = run(files, con, resume=args.resume)
    except SchemaDrift as e:
        print(f"STOPPED: {e}")
        return 2
    for name, err in download_failures:
        common.log_status(con, SOURCE, name, "failed", error=f"download: {err}")
    result.failures = download_failures + result.failures
    return common.report(result, SOURCE)


if __name__ == "__main__":
    raise SystemExit(main())
