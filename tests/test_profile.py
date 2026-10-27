import datetime as dt
import shutil

from conftest import FIXTURES, fixture_json

from ingest import nass, wasde
from scripts import profile_raw


def test_profile_reports_series_suppression_codes_and_wasde_attributes(tmp_path):
    con = nass.connect()
    con.execute(wasde.DDL)
    when = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    nass.load_partition(con, "annual_state", 2024, fixture_json("nass", "annual_state_2024.json")["data"], when)
    src = tmp_path / "in"
    src.mkdir()
    shutil.copy(FIXTURES / "wasde" / "oce-wasde-report-data-2025-01.csv", src)
    wasde.run(list(src.iterdir()), con)

    report = profile_raw.build_report(con)
    assert "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE" in report
    assert "(D)" in report                       # the suppression code is listed as a non-numeric value
    assert "Ending Stocks" in report and "Million Bushels" in report
    assert "nass" in report and "wasde" in report


def test_profile_runs_on_an_empty_database():
    from ingest import common
    report = profile_raw.build_report(common.connect())
    assert "raw.nass_quickstats is empty or missing" in report
