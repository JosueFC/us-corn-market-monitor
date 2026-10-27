"""Run the dbt project on synthetic data, then break the data on purpose and check that the intended test fails.

The first test builds the models on clean sample data and requires every test to pass. Each case below
then copies that database, damages one thing in the raw tables with SQL (a duplicate row, a unit mix-up,
a state total that no longer adds up, stale data, ...) and requires the matching data test to fail.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest
from dbt.adapters.factory import reset_adapters
from dbt.cli.main import dbtRunner

from scripts import make_sample_data

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"
LATEST_REPORT = "(SELECT max(try_cast(wasde_number AS INTEGER)) FROM raw.wasde)"
TODAY = dt.date.today()

NATIONAL_MARCH_2024_PRICE = (
    "query_name = 'price_received_national' AND short_desc = 'CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU' "
    "AND freq_desc = 'MONTHLY' AND \"year\" = '2024' AND reference_period_desc = 'MAR'"
)


@contextmanager
def patched_env(values: dict[str, str]):
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def dbt_build(db: Path, workdir: Path) -> dict[str, str]:
    """Run `dbt build` against the given database and return {node name: status}."""
    env = {
        "CORN_DB": str(db),
        "DBT_TARGET_PATH": str(workdir / "target"),
        "DBT_LOG_PATH": str(workdir / "logs"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "False",
    }
    with patched_env(env):
        try:
            outcome = dbtRunner().invoke(
                ["--quiet", "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
            )
        finally:
            reset_adapters()  # release the DuckDB file; Windows will not copy or delete a file that is still open
    assert outcome.exception is None, outcome.exception
    return {r.node.name: str(getattr(r.status, "value", r.status)) for r in outcome.result.results}


def failed(statuses: dict[str, str]) -> set[str]:
    return {name for name, status in statuses.items() if status in ("fail", "error")}


@pytest.fixture(scope="session")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("dbt")


@pytest.fixture(scope="session")
def clean_db(workdir):
    db = workdir / "clean.duckdb"
    assert make_sample_data.main(["--db", str(db)]) == 0
    return db


@pytest.fixture(scope="session")
def clean_run(clean_db, workdir):
    """Build on a copy so that `clean_db` stays untouched and can be copied for every damaged case."""
    built = workdir / "clean_built.duckdb"
    shutil.copy(clean_db, built)
    return dbt_build(built, workdir)


def test_clean_sample_data_passes_every_model_and_test(clean_run):
    problems = {name: status for name, status in clean_run.items() if status in ("fail", "error", "warn")}
    assert problems == {}
    assert len([n for n in clean_run if n.startswith("assert_")]) >= 15  # the singular tests ran
    assert any(n.startswith("unique_combination_") for n in clean_run)  # and the grain tests


# (case id, SQL that damages the raw data, test names that must fail)
BREAKS = [
    (
        "duplicate_price_row",
        f"INSERT INTO raw.nass_quickstats SELECT * FROM raw.nass_quickstats WHERE {NATIONAL_MARCH_2024_PRICE}",
        ["unique_combination_stg_nass__price_monthly"],
    ),
    (
        "state_production_no_longer_adds_up",
        "UPDATE raw.nass_quickstats SET value = "
        "cast(cast(round(cast(replace(value, ',', '') AS DOUBLE) * 1.1) AS BIGINT) "
        "AS VARCHAR) WHERE query_name = 'annual_state' AND state_alpha = 'IA' AND \"year\" = '2024' "
        "AND reference_period_desc = 'YEAR' AND short_desc = 'CORN, GRAIN - PRODUCTION, MEASURED IN BU'",
        ["assert_state_production_sums_to_national"],
    ),
    (
        "balance_sheet_identity_broken",
        f"UPDATE raw.wasde SET value = cast(cast(replace(value, ',', '') AS DOUBLE) + 50 AS VARCHAR) "
        f"WHERE attribute = 'Ending Stocks' AND try_cast(wasde_number AS INTEGER) = {LATEST_REPORT}",
        ["assert_wasde_balance_identity"],
    ),
    (
        "price_in_cents_instead_of_dollars",
        f"UPDATE raw.nass_quickstats SET unit_desc = 'CENTS / BU', value = '421' WHERE {NATIONAL_MARCH_2024_PRICE}",
        [
            "accepted_values_stg_nass__price_monthly_unit_desc",
            "between_values_stg_nass__price_monthly_price_usd_per_bu",
        ],
    ),
    (
        "wasde_unit_changed",
        f"UPDATE raw.wasde SET unit = 'Thousand Bushels' WHERE attribute = 'Production' "
        f"AND try_cast(wasde_number AS INTEGER) = {LATEST_REPORT}",
        ["assert_wasde_units_match_map"],
    ),
    (
        "missing_state_key",
        f"UPDATE raw.nass_quickstats SET state_fips_code = NULL WHERE {NATIONAL_MARCH_2024_PRICE}",
        ["not_null_stg_nass__price_monthly_state_key"],
    ),
    (
        "quick_stats_pull_is_stale",
        "UPDATE raw.nass_quickstats SET _pulled_at = _pulled_at - INTERVAL 400 DAY",
        ["assert_nass_data_is_fresh"],
    ),
    (
        "wasde_stops_updating",
        f"DELETE FROM raw.wasde WHERE try_cast(wasde_number AS INTEGER) > {LATEST_REPORT} - 6",
        ["assert_wasde_is_fresh"],
    ),
    (
        "latest_price_is_months_old",
        f"DELETE FROM raw.nass_quickstats WHERE query_name LIKE 'price_received%' AND freq_desc = 'MONTHLY' "
        f'AND cast("year" AS INTEGER) >= {TODAY.year - 1}',
        ["assert_latest_price_not_stale"],
    ),
    (
        "new_wasde_attribute_name",
        f"UPDATE raw.wasde SET attribute = 'Seed Corn Imports' WHERE attribute = 'Imports' "
        f"AND try_cast(wasde_number AS INTEGER) = {LATEST_REPORT}",
        ["assert_wasde_attributes_mapped"],
    ),
    (
        "value_in_unknown_format",
        f"UPDATE raw.nass_quickstats SET value = 'about 4' WHERE {NATIONAL_MARCH_2024_PRICE}",
        ["assert_nass_values_parse"],
    ),
    (
        "unexplained_missing_wasde_report",
        "DELETE FROM raw.wasde WHERE report_date = 'May 2024'",
        ["assert_wasde_report_gaps_are_known"],
    ),
    (
        "duplicate_wasde_line",
        f"INSERT INTO raw.wasde SELECT * FROM raw.wasde WHERE attribute = 'Exports' "
        f"AND try_cast(wasde_number AS INTEGER) = {LATEST_REPORT}",
        ["unique_combination_stg_wasde__corn_us"],
    ),
    (
        "release_date_in_new_format",
        f"UPDATE raw.wasde SET release_date = '10.12.2026' WHERE try_cast(wasde_number AS INTEGER) = {LATEST_REPORT}",
        ["not_null_stg_wasde__corn_us_release_date"],
    ),
    (
        "condition_classes_do_not_add_up",
        "UPDATE raw.nass_quickstats SET value = '60' WHERE query_name = 'condition_national' "
        "AND short_desc LIKE '%PCT GOOD' AND week_ending = (SELECT max(week_ending) FROM raw.nass_quickstats "
        "WHERE query_name = 'condition_national')",
        ["assert_condition_classes_sum_to_100"],
    ),
    (
        "yield_in_wrong_units",
        "UPDATE raw.nass_quickstats SET value = '21,000' WHERE query_name = 'annual_state' AND state_alpha = 'IL' "
        "AND \"year\" = '2022' AND reference_period_desc = 'YEAR' "
        "AND short_desc = 'CORN, GRAIN - YIELD, MEASURED IN BU / ACRE'",
        ["assert_nass_annual_units_and_ranges"],
    ),
]


@pytest.mark.parametrize("case, damage, expected", BREAKS, ids=[b[0] for b in BREAKS])
def test_damaged_data_is_caught(case, damage, expected, clean_run, clean_db, workdir):
    broken = workdir / f"{case}.duckdb"
    shutil.copy(clean_db, broken)
    con = duckdb.connect(str(broken))
    con.execute(damage)
    con.close()

    statuses = dbt_build(broken, workdir)
    caught = failed(statuses)
    for prefix in expected:
        assert any(name.startswith(prefix) for name in caught), f"{prefix} did not fail; failed: {sorted(caught)}"


def test_reissued_wasde_report_replaces_the_original(clean_db, workdir):
    """A -V2 file for a report already loaded wins, and nothing is double counted."""
    db = workdir / "reissue.duckdb"
    shutil.copy(clean_db, db)
    con = duckdb.connect(str(db))
    latest = con.execute(f"SELECT {LATEST_REPORT}").fetchone()[0]
    con.execute(
        "CREATE TEMP TABLE reissue AS SELECT * FROM raw.wasde WHERE try_cast(wasde_number AS INTEGER) = ? "
        "AND source_file NOT LIKE '%.zip'",
        [latest],
    )
    con.execute(
        "UPDATE reissue SET source_file = replace(source_file, '.csv', '-V2.csv'), "
        "member = replace(member, '.csv', '-V2.csv'), "
        "value = CASE WHEN attribute = 'Area Planted' THEN '99.9' ELSE value END"
    )
    con.execute("INSERT INTO raw.wasde SELECT * FROM reissue")
    n_inserted = con.execute("SELECT count(*) FROM raw.wasde WHERE source_file LIKE '%-V2.csv'").fetchone()[0]
    con.close()
    assert n_inserted > 0

    statuses = dbt_build(db, workdir)
    assert failed(statuses) == set()
    con = duckdb.connect(str(db))
    exports = con.execute(
        "SELECT area_planted FROM marts.fct_wasde_balance WHERE report_vintage_key = ? "
        "ORDER BY marketing_year_key DESC LIMIT 1",
        [latest],
    ).fetchone()[0]
    reissued = con.execute(
        "SELECT is_reissue FROM marts.dim_report_vintage WHERE report_vintage_key = ?", [latest]
    ).fetchone()[0]
    assert exports == 99.9
    assert reissued is True
