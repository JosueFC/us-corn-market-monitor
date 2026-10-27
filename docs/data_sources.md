# Data sources and point-in-time rules

The project forecasts the monthly US corn price received by farmers and compares the forecasts with
USDA's own projection. A backtest is only honest if each forecast uses information that was public
on its forecast date, so every source is loaded in a way that preserves *when* a number was known.

## NASS Quick Stats

API: <https://quickstats.nass.usda.gov/api/> (free key, at most 50,000 rows per request).
Loader: `python -m ingest.nass`, configured in [`config/nass_queries.yml`](../config/nass_queries.yml).

| Query | Content |
|---|---|
| `price_received_national`, `price_received_state` | Monthly and marketing-year price received for corn ($/bu) |
| `annual_national`, `annual_state` | Planted and harvested area, yield, production, including NASS's in-season forecasts |
| `stocks_national` | Quarterly grain stocks (on-farm, off-farm, total) |
| `condition_national`, `condition_state` | Weekly crop condition ratings (percent of acreage per class) |

How the loader handles the source:

* **Filters are validated, not assumed.** `--check-config` checks every configured value against
  `get_param_values` and confirms with `get_counts` that each query returns rows.
* **Revisions overwrite history in the API.** Quick Stats serves only the latest revision of each
  number. Each load saves the full response under `data/raw/nass/<query>/year=<year>/<timestamp>.json`,
  so the landing zone accumulates a dated record of what the API showed on each run. Historical
  preliminary values that were revised before the first run cannot be recovered from the API.
* **In-season forecasts are kept.** Annual queries are not filtered on the reference period, so
  NASS's monthly crop forecasts (for example `YEAR - AUG FORECAST`) load next to the final estimates.
* **Values stay as served.** `Value` is stored as text, including thousands separators and
  suppression codes such as `(D)` (withheld to avoid disclosing individual operations); the staging
  models type the values and treat the codes as missing.
* **Revisions are refreshed.** With `--resume`, partitions already loaded are skipped except the most
  recent years (`--refresh-years`, default 2). NASS also revises earlier state estimates after each
  Census of Agriculture, so a full reload without `--resume` is the way to pick those up.

## WASDE historical report data

Page: <https://www.usda.gov/historical-wasde-report-data-3>. One CSV per monthly report from 2021,
multi-year ZIP archives of CSVs for April 2010 to December 2020. Each file holds the data as the
report first published it; USDA does not revise these files.
Loader: `python -m ingest.wasde`.

* **Vintages are the unit of loading.** One partition is one CSV (a monthly file, or one CSV inside an
  archive). Each row keeps its `WasdeNumber`, `ReportDate` and `ReleaseDate`, which are what the
  forecasting stage uses to decide whether a number was public at a forecast date.
* **Re-issued reports are kept side by side.** A few files carry a `-V2` suffix. Both versions are
  loaded under their own file names, and the staging model selects one version per report.
* **The header is checked.** The required columns were verified against
  `oce-wasde-report-data-2021-01.csv` (October 2026). A missing column stops the run; the
  optional ones (`ReliabilityProjection`, `AnnualQuarterFlag`, `ReleaseTime`, `ForecastYear`,
  `ForecastMonth`) load as NULL when a file does not have them.
* **Attribute names come from the data.** `python -m ingest.wasde --inventory` lists every corn
  attribute and unit loaded, with the range of reports it appears in. The staging models map those
  names to balance-sheet items.

## FAS PSD Online (context only)

<https://apps.fas.usda.gov/psdonline/> holds the revised balance-sheet history. Revised figures are
not what a forecaster saw at the time, so PSD data is never a forecasting input.

## The forecasting target

The target is the **monthly US average price received for corn grain as currently published in
Quick Stats**, that is, after NASS's revision of the preliminary figure.

NASS releases a preliminary price for a month and revises it in a later release. Quick Stats keeps
only the revised series, and the preliminary history before the first data pull is not available
from the API. To keep backtests point-in-time without that history, a forecast made at a given
origin uses only months whose revised price had been published by then; the preliminary figure for
the most recent month is not used as an input.
