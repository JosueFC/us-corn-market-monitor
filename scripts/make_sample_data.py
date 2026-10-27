"""Load SYNTHETIC raw data into the database so the dbt models and their tests can run without the
real sources. The records copy the structure found in the real Quick Stats and WASDE data (series
names, reference periods, suppression codes, extra survey domains, attribute spelling changes, two
date formats, a month with no WASDE report) and go through the production loaders. None of the
numbers are USDA figures.

    python -m scripts.make_sample_data                 # writes data/corn.duckdb (or $CORN_DB)
    python -m scripts.make_sample_data --db path.duckdb

Messy cases included on purpose, because the models have to handle them:
  * Quick Stats values with thousands separators and the suppression codes (D), (NA), (Z), (S)
  * survey domains other than TOTAL, irrigated and silage series, and percent-of-parity price rows
  * a revised-in-batch load_time on each marketing year's monthly prices
  * WASDE attribute names in two spellings ("Ending Stocks" and "Ending stocks"), "Ethanol for Fuel"
    renamed "Ethanol & by-products", release dates as 01/12/2021 and 2026-10-09, quoted numbers with commas
  * reliability tables and world tables inside the same files, and CCC items that the models ignore
  * no report in October 2025 (the federal shutdown)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import random
from pathlib import Path

from ingest import common, nass, wasde

TODAY = dt.date.today()
FIRST_YEAR = 2008
STATES = [  # fips, alpha, name, share of national production
    ("19", "IA", "IOWA", 0.17),
    ("17", "IL", "ILLINOIS", 0.14),
    ("31", "NE", "NEBRASKA", 0.12),
    ("27", "MN", "MINNESOTA", 0.10),
    ("18", "IN", "INDIANA", 0.06),
    ("39", "OH", "OHIO", 0.04),
    ("55", "WI", "WISCONSIN", 0.04),
]
NATIONAL = ("99", "US", "US TOTAL")
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
# Marketing-year average price level by marketing year start (shapes only: ethanol boom, 2012 drought, 2021-22 spike).
PRICE_LEVEL = {
    2008: 4.8,
    2009: 3.5,
    2010: 4.6,
    2011: 6.2,
    2012: 7.0,
    2013: 5.0,
    2014: 3.7,
    2015: 3.6,
    2016: 3.4,
    2017: 3.3,
    2018: 3.6,
    2019: 3.6,
    2020: 4.3,
    2021: 6.2,
    2022: 6.8,
    2023: 4.8,
    2024: 4.0,
    2025: 4.2,
    2026: 4.1,
}
PRODUCTION = {
    2007: 13038,
    2008: 12092,
    2009: 13110,
    2010: 12447,
    2011: 12314,
    2012: 10780,
    2013: 13829,
    2014: 14216,
    2015: 13602,
    2016: 15148,
    2017: 14609,
    2018: 14340,
    2019: 13692,
    2020: 14182,
    2021: 15074,
    2022: 13651,
    2023: 15342,
    2024: 14867,
    2025: 15900,
    2026: 15500,
}


def wasde_months_available() -> list[dt.date]:
    """First of each month with a WASDE report, from April 2010 to now, without October 2025."""
    last = (
        dt.date(TODAY.year, TODAY.month, 1)
        if TODAY.day >= 12
        else (dt.date(TODAY.year, TODAY.month, 1) - dt.timedelta(days=1)).replace(day=1)
    )
    out, d = [], dt.date(2010, 4, 1)
    while d <= last:
        if d != dt.date(2025, 10, 1):
            out.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


# --------------------------------------------------------------------------- NASS


def quickstats(
    short_desc,
    stat,
    unit,
    value,
    *,
    agg,
    geo,
    year,
    freq,
    period,
    domain="TOTAL",
    load_time="",
    week_ending="",
    begin="00",
    end="00",
):
    fips, alpha, name = geo
    return {
        "source_desc": "SURVEY",
        "commodity_desc": "CORN",
        "class_desc": "ALL CLASSES",
        "prodn_practice_desc": "ALL PRODUCTION PRACTICES",
        "util_practice_desc": "GRAIN",
        "statisticcat_desc": stat,
        "unit_desc": unit,
        "short_desc": short_desc,
        "domain_desc": domain,
        "domaincat_desc": "NOT SPECIFIED" if domain == "TOTAL" else f"{domain}: (ALL)",
        "agg_level_desc": agg,
        "state_alpha": alpha,
        "state_name": name,
        "state_fips_code": fips,
        "year": str(year),
        "freq_desc": freq,
        "begin_code": begin,
        "end_code": end,
        "reference_period_desc": period,
        "Value": value,
        "group_desc": "FIELD CROPS",
        "location_desc": name,
        "week_ending": week_ending,
        "load_time": load_time,
        "CV (%)": "",
    }


def monthly_prices(rng: random.Random) -> dict[tuple[int, int], float]:
    """National price by (calendar year, month): the marketing-year level with a seasonal and a random-walk part."""
    out, wiggle = {}, 0.0
    for y in range(FIRST_YEAR, TODAY.year + 1):
        for m in range(1, 13):
            my = y if m >= 9 else y - 1
            wiggle = 0.8 * wiggle + rng.gauss(0, 0.04)
            seasonal = 0.04 * ((m - 9) % 12 - 5.5) / 5.5
            out[(y, m)] = max(1.5, PRICE_LEVEL.get(my, 4.0) * (1 + seasonal + wiggle))
    return out


def load_time_for(year: int, month: int, today: dt.date) -> str:
    """NASS reloads each marketing year's months in one batch at the end of September after the year closes;
    until then a month carries the date it was first published (about the end of the following month)."""
    my_start = year if month >= 9 else year - 1
    batch = dt.date(my_start + 1, 9, 29)
    if batch <= today:
        return f"{batch} 15:00:00.000"
    first_published = (dt.date(year, month, 1) + dt.timedelta(days=62)).replace(day=1) - dt.timedelta(days=1)
    return f"{min(first_published, today)} 15:00:00.000"


def build_nass(con, rng: random.Random) -> None:
    now = dt.datetime.now(dt.UTC)
    prices = monthly_prices(rng)
    last_month = (dt.date(TODAY.year, TODAY.month, 1) - dt.timedelta(days=45)).replace(day=1)  # published with a lag
    price_unit = "$ / BU"
    price_desc = "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU"
    parity_desc = "CORN, GRAIN - PRICE RECEIVED, MEASURED IN PCT OF PARITY"
    suppressed_state, suppressed_month = "OH", dt.date(2013, 2, 1)

    by_query: dict[tuple[str, int], list[dict]] = {}

    def put(query, year, rec):
        by_query.setdefault((query, year), []).append(rec)

    for (y, m), p in sorted(prices.items()):
        if dt.date(y, m, 1) > last_month:
            continue
        lt = load_time_for(y, m, TODAY)
        put(
            "price_received_national",
            y,
            quickstats(
                price_desc,
                "PRICE RECEIVED",
                price_unit,
                f"{p:.2f}",
                agg="NATIONAL",
                geo=NATIONAL,
                year=y,
                freq="MONTHLY",
                period=MONTHS[m - 1],
                load_time=lt,
                begin=f"{m:02d}",
                end=f"{m:02d}",
            ),
        )
        put(
            "price_received_national",
            y,
            quickstats(
                parity_desc,
                "PRICE RECEIVED",
                "PCT OF PARITY",
                f"{int(p * 6)}",
                agg="NATIONAL",
                geo=NATIONAL,
                year=y,
                freq="MONTHLY",
                period=MONTHS[m - 1],
                load_time=lt,
            ),
        )
        for fips, alpha, name, _ in STATES:
            sp = p * rng.uniform(0.96, 1.03)
            value = "(NA)" if (alpha, dt.date(y, m, 1)) == (suppressed_state, suppressed_month) else f"{sp:.2f}"
            put(
                "price_received_state",
                y,
                quickstats(
                    price_desc,
                    "PRICE RECEIVED",
                    price_unit,
                    value,
                    agg="STATE",
                    geo=(fips, alpha, name),
                    year=y,
                    freq="MONTHLY",
                    period=MONTHS[m - 1],
                    load_time=lt,
                ),
            )
    for my in range(FIRST_YEAR, TODAY.year + 1):  # annual prices, only for years that have closed
        batch = dt.date(my + 1, 9, 29)
        if batch <= TODAY:
            lt = f"{batch} 15:00:00.000"
            put(
                "price_received_national",
                my,
                quickstats(
                    price_desc,
                    "PRICE RECEIVED",
                    price_unit,
                    f"{PRICE_LEVEL.get(my, 4.0):.2f}",
                    agg="NATIONAL",
                    geo=NATIONAL,
                    year=my,
                    freq="ANNUAL",
                    period="MARKETING YEAR",
                    load_time=lt,
                ),
            )
        if my < TODAY.year:
            put(
                "price_received_national",
                my,
                quickstats(
                    price_desc,
                    "PRICE RECEIVED",
                    price_unit,
                    f"{PRICE_LEVEL.get(my, 4.0) * 0.99:.2f}",
                    agg="NATIONAL",
                    geo=NATIONAL,
                    year=my,
                    freq="ANNUAL",
                    period="YEAR",
                ),
            )

    # Annual acreage, yield, production
    for y in range(FIRST_YEAR, TODAY.year + 1):
        total_prod = PRODUCTION.get(y, 15000) * 1_000_000
        state_rows = []
        for fips, alpha, name, share in STATES:
            prod = int(total_prod * share * rng.uniform(0.97, 1.03))
            yld = round(rng.uniform(150, 215), 1)
            harvested = int(prod / yld)
            planted = int(harvested * 1.08)
            state_rows.append(((fips, alpha, name), prod, yld, harvested, planted))
        national_prod = sum(r[1] for r in state_rows)
        national_harv = sum(r[3] for r in state_rows)
        national_plant = sum(r[4] for r in state_rows)
        periods = ["YEAR - AUG FORECAST", "YEAR - SEP FORECAST"] + (["YEAR"] if y < TODAY.year else [])
        for period in periods:
            f = 1.0 if period == "YEAR" else rng.uniform(0.97, 1.03)
            for geo, prod, yld, harv, plant in state_rows:
                kw = dict(agg="STATE", geo=geo, year=y, freq="ANNUAL", period=period)
                put(
                    "annual_state",
                    y,
                    quickstats(
                        "CORN, GRAIN - PRODUCTION, MEASURED IN BU", "PRODUCTION", "BU", f"{int(prod * f):,}", **kw
                    ),
                )
                put(
                    "annual_state",
                    y,
                    quickstats(
                        "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE", "YIELD", "BU / ACRE", f"{yld * f:.1f}", **kw
                    ),
                )
                if period == "YEAR":
                    put(
                        "annual_state",
                        y,
                        quickstats("CORN, GRAIN - ACRES HARVESTED", "AREA HARVESTED", "ACRES", f"{harv:,}", **kw),
                    )
                    put(
                        "annual_state",
                        y,
                        quickstats("CORN - ACRES PLANTED", "AREA PLANTED", "ACRES", f"{plant:,}", **kw),
                    )
            kw = dict(agg="NATIONAL", geo=NATIONAL, year=y, freq="ANNUAL", period=period)
            nat_prod = national_prod if period == "YEAR" else int(national_prod * f)
            put(
                "annual_national",
                y,
                quickstats("CORN, GRAIN - PRODUCTION, MEASURED IN BU", "PRODUCTION", "BU", f"{nat_prod:,}", **kw),
            )
            put(
                "annual_national",
                y,
                quickstats(
                    "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE",
                    "YIELD",
                    "BU / ACRE",
                    f"{national_prod * f / national_harv:.1f}",
                    **kw,
                ),
            )
            if period == "YEAR":
                put(
                    "annual_national",
                    y,
                    quickstats("CORN, GRAIN - ACRES HARVESTED", "AREA HARVESTED", "ACRES", f"{national_harv:,}", **kw),
                )
                put(
                    "annual_national",
                    y,
                    quickstats("CORN - ACRES PLANTED", "AREA PLANTED", "ACRES", f"{national_plant:,}", **kw),
                )
        # rows the models must leave out: other survey domains, irrigated and silage series, a suppressed value
        for fips, alpha, name, _ in STATES:
            geo = (fips, alpha, name)
            put(
                "annual_state",
                y,
                quickstats(
                    "CORN, GRAIN - TREATED, MEASURED IN PCT OF AREA PLANTED, AVG",
                    "TREATED",
                    "PCT OF AREA PLANTED",
                    "97",
                    agg="STATE",
                    geo=geo,
                    year=y,
                    freq="ANNUAL",
                    period="YEAR",
                    domain="CHEMICAL, HERBICIDE",
                ),
            )
            put(
                "annual_state",
                y,
                quickstats(
                    "CORN, GRAIN, IRRIGATED - YIELD, MEASURED IN BU / ACRE",
                    "YIELD",
                    "BU / ACRE",
                    "(D)",
                    agg="STATE",
                    geo=geo,
                    year=y,
                    freq="ANNUAL",
                    period="YEAR",
                ),
            )
            put(
                "annual_state",
                y,
                quickstats(
                    "CORN, SILAGE - PRODUCTION, MEASURED IN TONS",
                    "PRODUCTION",
                    "TONS",
                    "1,234,000",
                    agg="STATE",
                    geo=geo,
                    year=y,
                    freq="ANNUAL",
                    period="YEAR",
                ),
            )
        put(
            "annual_national",
            y,
            quickstats(
                "CORN, GRAIN - PRODUCTION, MEASURED IN BU",
                "PRODUCTION",
                "BU",
                "(S)",
                agg="NATIONAL",
                geo=NATIONAL,
                year=y,
                freq="ANNUAL",
                period="YEAR",
                domain="TYPE OF OPERATION",
            ),
        )

    # Quarterly stocks
    for y in range(FIRST_YEAR, TODAY.year + 1):
        for month, period in [(3, "FIRST OF MAR"), (6, "FIRST OF JUN"), (9, "FIRST OF SEP"), (12, "FIRST OF DEC")]:
            if dt.date(y, month, 1) + dt.timedelta(days=20) > TODAY:
                continue
            total = int(rng.uniform(1.5e9, 11e9))
            on_farm = int(total * rng.uniform(0.45, 0.6))
            for desc, v in [
                ("CORN, GRAIN - STOCKS, MEASURED IN BU", total),
                ("CORN, ON FARM, GRAIN - STOCKS, MEASURED IN BU", on_farm),
                ("CORN, OFF FARM, GRAIN - STOCKS, MEASURED IN BU", total - on_farm),
            ]:
                put(
                    "stocks_national",
                    y,
                    quickstats(
                        desc,
                        "STOCKS",
                        "BU",
                        f"{v:,}",
                        agg="NATIONAL",
                        geo=NATIONAL,
                        year=y,
                        freq="POINT IN TIME",
                        period=period,
                    ),
                )

    # Weekly condition (recent years only, to keep the sample small)
    classes = ["VERY POOR", "POOR", "FAIR", "GOOD", "EXCELLENT"]
    for y in range(max(FIRST_YEAR, TODAY.year - 6), TODAY.year + 1):
        week_end = dt.date(y, 6, 1)
        week_end += dt.timedelta(days=(6 - week_end.weekday()) % 7)
        for week in range(0, 18):
            we = week_end + dt.timedelta(days=7 * week)
            if we > TODAY:
                break
            for geo, agg, query in [(NATIONAL, "NATIONAL", "condition_national")] + [
                ((s[0], s[1], s[2]), "STATE", "condition_state") for s in STATES[:4]
            ]:
                cuts = sorted(rng.sample(range(1, 100), 4))
                pcts = [cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1], cuts[3] - cuts[2], 100 - cuts[3]]
                for cls, pct in zip(classes, pcts, strict=True):
                    put(
                        query,
                        y,
                        quickstats(
                            f"CORN - CONDITION, MEASURED IN PCT {cls}",
                            "CONDITION",
                            f"PCT {cls}",
                            str(pct),
                            agg=agg,
                            geo=geo,
                            year=y,
                            freq="WEEKLY",
                            period=f"WEEK #{we.isocalendar().week:02d}",
                            week_ending=str(we),
                        ),
                    )

    for (query, year), records in sorted(by_query.items()):
        nass.load_partition(con, query, year, records, now)


# --------------------------------------------------------------------------- WASDE

WASDE_HEADER = [
    "WasdeNumber",
    "ReportDate",
    "ReportTitle",
    "Attribute",
    "ReliabilityProjection",
    "Commodity",
    "Region",
    "MarketYear",
    "ProjEstFlag",
    "AnnualQuarterFlag",
    "Value",
    "Unit",
    "ReleaseDate",
    "ReleaseTime",
    "ForecastYear",
    "ForecastMonth",
]
US_TITLE = "U.S. Feed Grain and Corn Supply and Use"


def balance_sheet(my: int, vintage: dt.date, rng: random.Random) -> dict[str, float]:
    """Balance sheet for marketing year `my` as seen in the report of `vintage`.

    The forecast error shrinks as the marketing year ends.
    """
    months_to_end = max(0, (my + 1 - vintage.year) * 12 + 8 - vintage.month)
    err = min(1.0, months_to_end / 18) * 0.05
    prod = PRODUCTION.get(my, 15000) * (1 + rng.gauss(0, err))
    prev_prod = PRODUCTION.get(my - 1, 14000)
    beg = prev_prod * 0.12 * (7.5 - PRICE_LEVEL.get(my - 1, 4.0)) / 3.5 + 600
    imports = 25.0
    stu_true = max(0.04, 0.34 - 0.045 * PRICE_LEVEL.get(my, 4.0))
    supply = beg + prod + imports
    use = supply / (1 + stu_true) * (1 + rng.gauss(0, err * 0.5))
    exports = use * 0.16 * (1 + rng.gauss(0, err))
    domestic = use - exports
    ethanol = domestic * 0.37
    fsi = ethanol / 0.72
    feed = domestic - fsi
    ending = supply - use
    stu = ending / use
    price = PRICE_LEVEL.get(my, 4.0) * (1 - 1.2 * (stu - stu_true)) * (1 + rng.gauss(0, 0.02))
    harvested = 83.0 + (my % 5) * 0.7
    return {
        "beg": round(beg),
        "prod": round(prod),
        "imp": round(imports),
        "feed": round(feed),
        "fsi": round(fsi),
        "eth": round(ethanol),
        "exp": round(exports),
        "price": round(price, 2),
        "harv": round(harvested, 1),
        "plant": round(harvested + 6.5, 1),
        "yield": round(prod / harvested, 1),
    }


def report_rows(number: int, month: dt.date, rng: random.Random) -> list[list[str]]:
    release = month.replace(day=10)
    iso_dates = month.year >= 2021
    release_text = release.isoformat() if iso_dates else release.strftime("%m/%d/%Y")
    report_date = month.strftime("%B %Y")
    newest = month.year if month.month >= 5 else month.year - 1
    comma = 2016 <= month.year <= 2020
    lower = month == dt.date(2013, 6, 1)  # one report used lower-case attribute names, as in the real files

    def fmt(v):
        return f"{v:,}" if comma and isinstance(v, int) else str(v)

    def name(label):
        return label.lower() if lower and label not in ("Production", "Imports", "Exports") else label

    rows = []

    def add(title, commodity, region, attr, my_label, flag, value, unit):
        rows.append(
            [
                str(number),
                report_date,
                title,
                attr,
                "",
                commodity,
                region,
                my_label,
                flag,
                "Annual",
                fmt(value),
                unit,
                release_text,
                "12:00:00",
                str(month.year),
                str(month.month),
            ]
        )

    for my in (newest - 2, newest - 1, newest):
        flag = "Proj." if my == newest else ("" if month.year < 2014 and my == newest - 2 else "Est.")
        b = balance_sheet(my, release, rng)
        supply = b["beg"] + b["prod"] + b["imp"] + rng.choice([0, 0, 0, 1, -1])
        domestic = b["feed"] + b["fsi"]
        use = domestic + b["exp"] + rng.choice([0, 0, 0, 1, -1])
        ending = supply - use + rng.choice([0, 0, 0, 1, -1])
        my_label = f"{my}/{(my + 1) % 100:02d}"
        eth_name = "Ethanol for Fuel" if month.year < 2014 else "Ethanol & by-products"
        for attr, value, unit in [
            ("Area Planted", b["plant"], "Million Acres"),
            ("Area Harvested", b["harv"], "Million Acres"),
            ("Yield per Harvested Acre", b["yield"], "Bushels"),
            ("Beginning Stocks", b["beg"], "Million Bushels"),
            ("Production", b["prod"], "Million Bushels"),
            ("Imports", b["imp"], "Million Bushels"),
            ("Supply, Total", supply, "Million Bushels"),
            ("Feed and Residual", b["feed"], "Million Bushels"),
            ("Food, Seed & Industrial", b["fsi"], "Million Bushels"),
            (eth_name, b["eth"], "Million Bushels"),
            ("Domestic, Total", domestic, "Million Bushels"),
            ("Exports", b["exp"], "Million Bushels"),
            ("Use, Total", use, "Million Bushels"),
            ("Ending Stocks", ending, "Million Bushels"),
            ("Avg. Farm Price", b["price"], "$/bu"),
        ]:
            add(US_TITLE, "Corn", "United States", name(attr), my_label, flag, value, unit)
        if my == newest and month.month % 2 == 0:
            add(
                US_TITLE,
                "Corn",
                "United States",
                "Avg. Farm Price - High",
                my_label,
                flag,
                round(b["price"] + 0.4, 2),
                "$/bu",
            )
            add(
                US_TITLE,
                "Corn",
                "United States",
                "Avg. Farm Price - Low",
                my_label,
                flag,
                round(b["price"] - 0.4, 2),
                "$/bu",
            )
        if month.year < 2013:
            for attr in ("CCC Inventory", "Free Stocks", "Outstanding Loans"):
                add(US_TITLE, "Corn", "United States", attr, my_label, flag, 100, "Million Bushels")
    # rows in the same files that the models must not pick up
    add(
        f"Reliability of United States {month.strftime('%B')} Projections",
        "Corn",
        "United States",
        "Production",
        "",
        "",
        3.1,
        "Percent",
    )
    add(
        "World Corn Supply and Use",
        "Corn",
        "United States",
        "Beginning Stocks",
        f"{newest}/{(newest + 1) % 100:02d}",
        "Proj.",
        55.2,
        "Million Metric Tons",
    )
    add(
        "U.S. Wheat Supply and Use",
        "Wheat",
        "United States",
        "Production",
        f"{newest}/{(newest + 1) % 100:02d}",
        "Proj.",
        1812,
        "Million Bushels",
    )
    return rows


def build_wasde(con, rng: random.Random) -> None:
    by_file: dict[str, list[list[str]]] = {}
    number = 481
    for month in wasde_months_available():
        rows = report_rows(number, month, rng)
        number += 1
        if month.year <= 2015:
            fname = "oce-wasde-report-data-2010-04-to-2015-12.zip"
        elif month.year <= 2020:
            fname = "oce-wasde-report-data-2016-01-to-2020-12.zip"
        else:
            fname = f"oce-wasde-report-data-{month.year}-{month.month:02d}.csv"
        by_file.setdefault(fname, []).extend(rows)
    for fname, rows in by_file.items():
        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(WASDE_HEADER)
        writer.writerows(rows)
        member = fname.replace(".zip", ".csv")
        wasde.load_member(con, fname, member, buf.getvalue().encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.make_sample_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--db", type=Path, help="database file (default: $CORN_DB or data/corn.duckdb)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    db = args.db or common.db_path()
    if db.exists():
        raise SystemExit(
            f"{db} already exists; the sample loader writes to a fresh database only. "
            "Delete it or pass --db with a new path."
        )
    con = common.connect(db, nass.DDL + "\n" + wasde.DDL)
    rng = random.Random(args.seed)
    build_nass(con, rng)
    build_wasde(con, rng)
    n_nass = con.execute("select count(*) from raw.nass_quickstats").fetchone()[0]
    n_wasde = con.execute("select count(*) from raw.wasde").fetchone()[0]
    print(f"loaded {n_nass} synthetic Quick Stats rows and {n_wasde} synthetic WASDE rows into {db}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
