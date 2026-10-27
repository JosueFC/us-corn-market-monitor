# Data model and tests

Layers, all in the DuckDB file written by the ingestion scripts (`data/corn.duckdb`):

```
raw.nass_quickstats, raw.wasde, raw.ingest_log        written by python -m ingest.*
        |
staging.*         one view per subject: filter to the corn series, parse text into types,
                  normalize names and dates. No business logic.
intermediate.*    pivots and reconciliations: the balance sheet with one column per item,
                  final annual estimates, production against the sum of states.
marts.*           the star schema loaded by Power BI (dimensions and facts below).
```

Run from the `dbt/` folder: `dbt seed`, `dbt build` (models, seeds and tests together).

## Marts

| Table | Grain | Notes |
|---|---|---|
| `dim_date` | one row per day, 1975-2035 | calendar year, month, quarter and corn marketing year (Sept-Aug) columns |
| `dim_marketing_year` | one row per marketing year | key is the calendar year in which the marketing year starts |
| `dim_state` | one row per state, plus the national total (`99`) | |
| `dim_report_vintage` | one row per WASDE report | `release_date` is when the report became public |
| `dim_model` | one row per forecast model or benchmark | seed; the R stage writes facts keyed by `model_key` |
| `fct_price_monthly` | state and month | national series has state key 99; carries both availability dates |
| `fct_production_annual_state` | state and crop year | final estimates, states only |
| `fct_production_national_reconciliation` | crop year | national production against the sum of states |
| `fct_wasde_balance` | report and marketing year | balance sheet exactly as published in that report |
| `fct_marketing_year_summary` | marketing year | NASS marketing-year price with the latest WASDE balance sheet |
| `fct_crop_condition_weekly` | state and week ending | |
| `fct_grain_stocks_quarterly` | stocks date | |

The forecast fact (model, horizon, forecast date) is written by the R stage.

## Data tests

Generic tests live in `dbt/tests/generic/`, singular tests in `dbt/tests/`, column tests in the `schema.yml`
files. Thresholds are dbt variables in `dbt/dbt_project.yml`.

| Requirement | Test |
|---|---|
| State production adds up to the national total | `assert_state_production_sums_to_national` (final estimates, error), `assert_state_forecasts_sum_to_national` (in-season forecasts, warning) |
| WASDE balance identities: beginning stocks + production + imports = total supply = total use + ending stocks | `assert_wasde_balance_identity` (error), `assert_wasde_domestic_identity` (warning), `assert_wasde_core_items_present` |
| Uniqueness at each table's grain | `unique_combination` on every staging model and fact |
| Not-null keys | `not_null` on keys; `relationships` from every fact key to its dimension |
| Freshness | `assert_nass_data_is_fresh`, `assert_wasde_is_fresh`, `assert_latest_price_not_stale` |
| Units and magnitudes | `accepted_values` on unit columns, `between_values` on prices, yields and ratios, `assert_wasde_units_match_map`, `assert_nass_annual_units_and_ranges` |
| New or renamed source fields | `assert_wasde_attributes_mapped`, `assert_nass_values_parse`, `assert_wasde_market_year_format`, `assert_wasde_report_gaps_are_known` (see `seeds/known_wasde_gaps.csv`) |

`tests/test_dbt_models.py` builds the models on synthetic data, then damages the raw data in 15 different ways
(a duplicate row, a state total that no longer adds up, a broken balance sheet, a price in cents, stale
data, an unexplained missing report, and others) and checks that the matching test fails. It also checks
that a re-issued `-V2` WASDE file replaces the original.

When a test fails, `dbt build` skips the models downstream of it, so a data problem stops before it
reaches the marts.
