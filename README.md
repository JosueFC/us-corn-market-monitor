# US corn market monitor

A point-in-time data pipeline for the US corn market. It loads USDA's farm statistics (NASS Quick
Stats) and every monthly WASDE report as originally published, so that price forecasts can be
backtested using only the information available on each forecast date.

**Business question.** For a grain buyer or farm lender: where are US corn supply and prices heading,
and how far ahead can the monthly price received by farmers be forecast better than a naive guess
and better than USDA's own projection?

## Data

| Source | What is loaded | Loader |
|---|---|---|
| [NASS Quick Stats API](https://quickstats.nass.usda.gov/api/) | Monthly and marketing-year price received; acreage, yield and production by state; quarterly stocks; weekly crop condition | `ingest/nass.py` |
| [WASDE historical report data](https://www.usda.gov/historical-wasde-report-data-3) | Every report since April 2010 as first published: the US corn balance sheet and USDA's projected season-average farm price | `ingest/wasde.py` |

[`docs/data_sources.md`](docs/data_sources.md) explains how each source is loaded, how revisions
are handled and how the forecasting target is defined.

## Ingestion design

* **Raw landing zone.** Every API response and downloaded file is saved untouched under `data/raw/`
  before it is parsed.
* **Idempotent partition loads.** A partition (a NASS query and year, or one WASDE CSV) is deleted
  and re-inserted in a single transaction, so reruns never duplicate rows and a failure never leaves
  a partial partition.
* **Schema checks.** Missing fields or columns stop the run (`SchemaDrift`); truncated files, empty
  results and responses at the API's 50,000-row limit are rejected rather than loaded.
* **Retries and a failure log.** Timeouts and 429/5xx responses are retried with exponential backoff.
  Every attempt is recorded in `raw.ingest_log`; one failed partition does not stop the others, and
  the command exits with a non-zero code when anything failed.
* **`--resume`.** Reruns skip partitions already loaded (WASDE: with identical content), retry the
  ones that failed and refresh the most recent NASS years, which are still being revised.
* **Secrets.** The NASS key is read from the `NASS_API_KEY` environment variable and removed from
  every message, log entry and landing-zone file.

## How to run

Requires Python 3.12 or later. Get a free NASS key at <https://quickstats.nass.usda.gov/api/>.

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:NASS_API_KEY = "<your key>"     # this session only
# to keep it for future sessions: [Environment]::SetEnvironmentVariable("NASS_API_KEY", "<your key>", "User")

python -m ingest.nass --check-config   # verify every configured filter value against the API
python -m ingest.nass                  # load all queries (rerun with --resume after an interruption)
python -m ingest.wasde --download      # find, download and load every WASDE file
python -m ingest.wasde --inventory     # list the corn attributes and units that were loaded
```

**bash**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NASS_API_KEY="<your key>"

python -m ingest.nass --check-config
python -m ingest.nass
python -m ingest.wasde --download
python -m ingest.wasde --inventory
```

If the USDA site refuses automated downloads, download the WASDE files in a browser into
`data/manual/wasde/` and load them with `python -m ingest.wasde --from-dir data/manual/wasde`.

The database is written to `data/corn.duckdb` and the landing zone to `data/raw/`; set `CORN_DB`
and `CORN_RAW_DIR` to use other locations.

## Tests

```
ruff check .
python -m pytest
```

The tests run without network access on synthetic fixtures that copy the structure of the real
responses and files ([`tests/fixtures/README.md`](tests/fixtures/README.md)). Several fixtures are
broken on purpose (a missing field, a truncated file, a response at the row limit) to show that each
check fails when it should. GitHub Actions runs lint and tests on Ubuntu and Windows.

## Repository layout

```
config/nass_queries.yml   NASS queries (filters and start year)
docs/                     data sources and point-in-time rules
ingest/                   loaders: common.py (shared), nass.py, wasde.py
tests/                    pytest suite and fixtures
```

## License

MIT
