"""Write a plain-text profile of the built marts, to check them against what the forecasting step needs.

    python -m scripts.profile_marts                  # prints the report
    python -m scripts.profile_marts --out data/marts_profile.txt

Run `dbt build` first. The report shows the size and date range of each mart, gaps in the national price
series, how long after the price month each price was loaded by NASS (which decides how much of the price
history is usable as "known at the time"), WASDE report coverage and the most recent balance sheet.
It contains no credentials.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path

import duckdb
import pandas as pd

from ingest import common

NATIONAL = "99"
MARTS = [
    "dim_date",
    "dim_state",
    "dim_marketing_year",
    "dim_report_vintage",
    "fct_price_monthly",
    "fct_wasde_balance",
    "fct_marketing_year_summary",
    "fct_production_annual_state",
    "fct_production_national_reconciliation",
    "fct_grain_stocks_quarterly",
    "fct_crop_condition_weekly",
]


def _show(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    print(df.to_string(index=False) if len(df) else "(no rows)")


def _q(con, sql: str, params: list | None = None) -> pd.DataFrame:
    return con.execute(sql, params or []).df()


def build_report(con: duckdb.DuckDBPyConnection) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        existing = set(
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'"
            ).fetchall()
        )
        if "fct_price_monthly" not in existing:
            print("The marts have not been built; run `dbt build --project-dir dbt --profiles-dir dbt` first.")
            return buf.getvalue()

        _show(
            "Rows per mart",
            pd.DataFrame(
                [(t, con.execute(f"SELECT count(*) FROM marts.{t}").fetchone()[0]) for t in MARTS if t in existing],
                columns=["mart", "rows"],
            ),
        )

        _show(
            "National monthly price: coverage",
            _q(
                con,
                "SELECT min(price_month_key) AS first_month, max(price_month_key) AS last_month, count(*) AS months, "
                "count(price_usd_per_bu) AS with_price, min(price_usd_per_bu) AS min_price, "
                "max(price_usd_per_bu) AS max_price FROM marts.fct_price_monthly WHERE state_key = ?",
                [NATIONAL],
            ),
        )
        _show(
            "National monthly price: months missing between first and last",
            _q(
                con,
                "WITH m AS (SELECT cast(x AS date) AS month FROM generate_series("
                "(SELECT min(price_month_key) FROM marts.fct_price_monthly WHERE state_key = ?), "
                "(SELECT max(price_month_key) FROM marts.fct_price_monthly WHERE state_key = ?), "
                "interval 1 month) AS t(x)) "
                "SELECT m.month FROM m LEFT JOIN marts.fct_price_monthly p "
                "ON p.price_month_key = m.month AND p.state_key = ? WHERE p.price_usd_per_bu IS NULL ORDER BY 1",
                [NATIONAL, NATIONAL, NATIONAL],
            ),
        )
        _show(
            "National monthly price: months between the price month and the NASS load of the value now stored",
            _q(
                con,
                "SELECT date_diff('month', price_month_key, available_date_revision) AS months_to_load, "
                "count(*) AS n_prices, min(price_month_key) AS first_month, max(price_month_key) AS last_month "
                "FROM marts.fct_price_monthly WHERE state_key = ? GROUP BY 1 ORDER BY 1",
                [NATIONAL],
            ),
        )
        _show(
            "National monthly price: latest 8 months",
            _q(
                con,
                "SELECT price_month_key, price_usd_per_bu, available_date_lag, available_date_revision "
                "FROM marts.fct_price_monthly WHERE state_key = ? ORDER BY price_month_key DESC LIMIT 8",
                [NATIONAL],
            ),
        )

        _show(
            "WASDE reports",
            _q(
                con,
                "SELECT count(*) AS reports, min(release_date) AS first_release, max(release_date) AS last_release, "
                "count(*) FILTER (WHERE is_reissue) AS reissued FROM marts.dim_report_vintage",
            ),
        )
        _show(
            "WASDE balance sheets: rows with the fields the forecasting step uses, by release year",
            _q(
                con,
                "SELECT year(v.release_date) AS release_year, count(DISTINCT b.report_vintage_key) AS reports, "
                "count(*) AS rows, count(b.stocks_to_use) AS with_stocks_to_use, "
                "count(b.season_avg_price) AS with_season_avg_price "
                "FROM marts.fct_wasde_balance b JOIN marts.dim_report_vintage v USING (report_vintage_key) "
                "GROUP BY 1 ORDER BY 1",
            ),
        )
        _show(
            "Latest WASDE report: balance sheets",
            _q(
                con,
                "SELECT b.marketing_year_key, b.status, b.production, b.use_total, b.ending_stocks, b.stocks_to_use, "
                "b.season_avg_price FROM marts.fct_wasde_balance b WHERE b.report_vintage_key = "
                "(SELECT max(report_vintage_key) FROM marts.fct_wasde_balance) ORDER BY 1",
            ),
        )
        _show(
            "Marketing years: NASS price against the last WASDE season-average price",
            _q(
                con,
                "SELECT marketing_year_key, nass_marketing_year_price, latest_wasde_season_avg_price, "
                "latest_wasde_status FROM marts.fct_marketing_year_summary ORDER BY 1 DESC LIMIT 12",
            ),
        )
        _show(
            "State production against the national figure (largest differences)",
            _q(
                con,
                "SELECT * FROM marts.fct_production_national_reconciliation ORDER BY relative_difference DESC LIMIT 5",
            ),
        )
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="write the report to this file as well as printing it")
    args = ap.parse_args(argv)
    con = common.connect()
    report = build_report(con)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"Report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
