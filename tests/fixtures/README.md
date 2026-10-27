# Test fixtures

All fixtures are **synthetic**. They copy the structure of the real sources so the loaders can be
tested without network access, but none of the numbers are USDA figures.

| Fixture | Mimics | Used to prove |
|---|---|---|
| `nass/price_received_national_2024.json` | Quick Stats `api_GET` JSON: 12 monthly prices and a marketing-year price | normal load, idempotent reload, landing-zone snapshot |
| `nass/annual_state_2024.json` | Annual state estimates with an August yield forecast and a withheld `(D)` value | values such as `2,550,000,000` and `(D)` are stored exactly as served |
| `nass/get_counts.json`, `nass/get_param_values_statisticcat_desc.json` | The two discovery endpoints | `--check-config` accepts valid values and flags unknown ones |
| `nass/error_exceeds_limit.json` | The error body for a query over 50,000 rows | the loader refuses to load a possibly truncated result |
| `nass/error_bad_request.json`, `nass/error_unauthorized.json` | Other error bodies | empty years are logged as `no_data`; a rejected key stops the run |
| `nass/broken_missing_value_field.json` | **Broken:** records without the `Value` field | schema drift is detected and nothing is loaded |
| `nass/broken_no_data_key.json` | **Broken:** a body without the `data` list | schema drift is detected |
| `wasde/oce-wasde-report-data-2025-01.csv` | One monthly WASDE file with a balanced US corn balance sheet (header verified against a published file) | normal load, inventory of corn attributes |
| `wasde/oce-wasde-report-data-2025-01-V2.csv` | The same report re-issued with a corrected export figure | both versions are kept under their own file names |
| `wasde/older_layout_2010-05.csv` | A file without the optional columns | optional columns load as NULL |
| `wasde/historical_wasde_page.html` | The USDA download page | link discovery: relative and absolute links, `-V2` files, ZIP archives, duplicates, unrelated links |
| `wasde/broken_missing_value_column.csv` | **Broken:** `Value` renamed | schema drift is detected |
| `wasde/broken_truncated.csv` | **Broken:** file cut off mid-row | the file is rejected, the table is untouched |
| `wasde/broken_header_only.csv` | **Broken:** no data rows | the file is rejected |

The NASS field names follow the Quick Stats JSON records; the WASDE corn attribute names
(for example `Ending Stocks`) are illustrative. The loaders do not depend on attribute names, and the
names used downstream are taken from `python -m ingest.wasde --inventory` run on the real files.
