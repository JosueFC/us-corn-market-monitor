"""Write a plain-text profile of what is in the raw tables, to design and check the staging models.

    python -m scripts.profile_raw                  # prints the report
    python -m scripts.profile_raw --out data/profile.txt

The report lists, for each NASS query, the distinct series (short description, domain, unit,
frequency) with their year ranges and row counts, the values that are not plain numbers
(suppression codes such as "(D)"), the WASDE corn attributes and units, and the load log summary.
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


def _table_exists(con, name: str) -> bool:
    schema, table = name.split(".")
    return (
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?", [schema, table]
        ).fetchone()[0]
        > 0
    )


def _show(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    print(df.to_string(index=False) if len(df) else "(no rows)")


def build_report(con: duckdb.DuckDBPyConnection) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf), pd.option_context("display.width", 250, "display.max_colwidth", 90):
        if _table_exists(con, "raw.nass_quickstats"):
            _show(
                "NASS rows per query",
                con.execute(
                    "SELECT query_name, count(*) AS n_rows, min(year) AS first_year, max(year) AS last_year, "
                    "max(_pulled_at)::DATE AS last_pulled FROM raw.nass_quickstats GROUP BY ALL ORDER BY 1"
                ).df(),
            )
            _show(
                "NASS domains per query (what the extra rows in some years are)",
                con.execute(
                    "SELECT query_name, domain_desc, count(*) AS n_rows, min(year) AS first_year, "
                    "max(year) AS last_year "
                    "FROM raw.nass_quickstats GROUP BY ALL ORDER BY 1, 3 DESC"
                ).df(),
            )
            _show(
                "NASS series in the TOTAL domain: description, unit, frequency, periods, years",
                con.execute(
                    "SELECT query_name, short_desc, unit_desc, freq_desc, "
                    "count(DISTINCT reference_period_desc) AS n_periods, count(*) AS n_rows, "
                    "min(year) AS first_year, max(year) AS last_year "
                    "FROM raw.nass_quickstats WHERE domain_desc = 'TOTAL' GROUP BY ALL ORDER BY 1, 2, 3, 4"
                ).df(),
            )
            _show(
                "NASS reference periods by frequency",
                con.execute(
                    "SELECT freq_desc, "
                    "string_agg(DISTINCT reference_period_desc, ' | ' ORDER BY reference_period_desc) "
                    "AS periods FROM raw.nass_quickstats GROUP BY ALL ORDER BY 1"
                ).df(),
            )
            _show(
                "NASS values that are not plain numbers",
                con.execute(
                    "SELECT value, count(*) AS n_rows FROM raw.nass_quickstats "
                    "WHERE try_cast(replace(value, ',', '') AS DOUBLE) IS NULL GROUP BY ALL ORDER BY 2 DESC LIMIT 25"
                ).df(),
            )
            _show(
                "NASS price rows in 2025 (national)",
                con.execute(
                    "SELECT short_desc, freq_desc, reference_period_desc, value, unit_desc, load_time "
                    "FROM raw.nass_quickstats WHERE query_name = 'price_received_national' AND year = '2025' "
                    "ORDER BY 1, 2, begin_code, 3"
                ).df(),
            )
        else:
            print("\nraw.nass_quickstats is empty or missing")
        if _table_exists(con, "raw.wasde"):
            _show(
                "WASDE files loaded",
                con.execute(
                    "SELECT count(DISTINCT source_file) AS files, count(DISTINCT wasde_number) AS reports, "
                    "count(*) AS n_rows, min(try_cast(wasde_number AS INTEGER)) AS first_report, "
                    "max(try_cast(wasde_number AS INTEGER)) AS last_report FROM raw.wasde"
                ).df(),
            )
            _show(
                "WASDE corn (United States): report titles, attributes, units",
                con.execute(
                    "SELECT report_title, attribute, unit, count(DISTINCT wasde_number) AS n_reports, "
                    "count(DISTINCT market_year) AS n_market_years, string_agg(DISTINCT proj_est_flag, ',') AS flags "
                    "FROM raw.wasde WHERE commodity ILIKE '%corn%' AND region = 'United States' "
                    "GROUP BY ALL ORDER BY 1, 2, 3"
                ).df(),
            )
            _show(
                "WASDE other regions and commodity names containing corn",
                con.execute(
                    "SELECT commodity, region, count(*) AS n_rows FROM raw.wasde WHERE commodity ILIKE '%corn%' "
                    "GROUP BY ALL ORDER BY 3 DESC LIMIT 20"
                ).df(),
            )
            _show(
                "WASDE reports with more than one file version",
                con.execute(
                    "SELECT wasde_number, report_date, string_agg(DISTINCT source_file, ' | ') AS files "
                    "FROM raw.wasde GROUP BY ALL HAVING count(DISTINCT source_file) > 1 ORDER BY 1"
                ).df(),
            )
            _show(
                "WASDE release dates (first and last, as text)",
                con.execute("SELECT min(release_date) AS min_text, max(release_date) AS max_text FROM raw.wasde").df(),
            )
        else:
            print("\nraw.wasde is empty or missing")
        _show(
            "Load log summary",
            con.execute("SELECT source, status, count(*) AS n FROM raw.ingest_log GROUP BY ALL ORDER BY 1, 2").df(),
        )
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.profile_raw", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, help="write the report to this file instead of printing it")
    args = ap.parse_args(argv)
    report = build_report(common.connect())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out} ({len(report.splitlines())} lines)")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
