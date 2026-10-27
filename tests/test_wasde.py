import shutil
import zipfile

import pytest
from conftest import FIXTURES, FakeResponse, FakeSession, fixture_bytes

from ingest import common, wasde

MONTHLY = "oce-wasde-report-data-2025-01.csv"
MONTHLY_V2 = "oce-wasde-report-data-2025-01-V2.csv"


def copy_fixtures(dest, *names):
    dest.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copy(FIXTURES / "wasde" / n, dest / n)
    return [dest / n for n in names]


def count(con, where=""):
    return con.execute(f"SELECT count(*) FROM raw.wasde {where}").fetchone()[0]


# ----------------------------------------------------------------- link discovery and download


def test_discover_links_resolves_relative_links_and_drops_duplicates_and_other_files():
    html = fixture_bytes("wasde", "historical_wasde_page.html").decode()
    links = wasde.discover_links(html, "https://www.usda.gov/historical-wasde-report-data-3")
    assert links == [
        "https://usda.azureedge.us/sites/default/files/documents/oce-wasde-report-data-2010-04-to-2015-12.zip",
        "https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2026-06-V2.csv",
        "https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2026-10.csv",
    ]


def test_download_saves_the_file_untouched(tmp_path):
    body = fixture_bytes("wasde", MONTHLY)
    path = wasde.download(f"https://www.usda.gov/x/{MONTHLY}", tmp_path, FakeSession(FakeResponse(200, body)))
    assert path.name == MONTHLY and path.read_bytes() == body


def test_blocked_download_explains_the_manual_route(tmp_path):
    with pytest.raises(wasde.DownloadBlocked, match="--from-dir data/manual/wasde"):
        wasde.download(f"https://www.usda.gov/x/{MONTHLY}", tmp_path, FakeSession(FakeResponse(403, "denied")))


def test_page_without_data_links_is_schema_drift(monkeypatch):
    monkeypatch.setattr(wasde, "fetch_page", lambda session=None: "<html><a href='/other.pdf'>x</a></html>")
    with pytest.raises(common.SchemaDrift):
        wasde.download_all(FakeSession(FakeResponse(200)))


# ----------------------------------------------------------------- parsing


def test_parse_keeps_text_and_maps_columns():
    df = wasde.parse_csv(fixture_bytes("wasde", MONTHLY), MONTHLY)
    assert len(df) == 29
    assert list(df.columns) == list(wasde.COLUMN_MAP.values())
    corn = df[(df.commodity == "Corn") & (df.attribute == "Ending Stocks") & (df.market_year == "2024/25")]
    assert corn.value.tolist() == ["1465"]
    assert corn.release_date.tolist() == ["01/10/2025"]


def test_byte_order_mark_and_windows_1252_are_handled():
    data = fixture_bytes("wasde", MONTHLY)
    assert len(wasde.parse_csv(b"\xef\xbb\xbf" + data, "bom")) == 29
    cp1252 = data.replace(b"Food, Seed & Industrial", "Food, Seed & Industrial – total".encode("cp1252"))
    df = wasde.parse_csv(cp1252, "cp1252")
    assert df.attribute.str.contains("–").any()


def test_older_layout_loads_optional_columns_as_null():
    df = wasde.parse_csv(fixture_bytes("wasde", "older_layout_2010-05.csv"), "old")
    assert len(df) == 2 and df.release_time.isna().all() and df.forecast_month.isna().all()


@pytest.mark.parametrize(
    "name, error",
    [
        ("broken_missing_value_column.csv", common.SchemaDrift),
        ("broken_truncated.csv", common.MalformedFile),
        ("broken_header_only.csv", common.MalformedFile),
    ],
)
def test_broken_files_are_rejected(name, error):
    with pytest.raises(error):
        wasde.parse_csv(fixture_bytes("wasde", name), name)


def test_empty_file_is_rejected():
    with pytest.raises(common.MalformedFile, match="empty"):
        wasde.parse_csv(b"", "empty.csv")


# ----------------------------------------------------------------- loading and runs


def test_reload_is_idempotent(tmp_path):
    con = wasde.connect()
    files = copy_fixtures(tmp_path / "in", MONTHLY)
    wasde.run(files, con)
    wasde.run(files, con)
    assert count(con) == 29


def test_both_versions_of_a_reissued_report_are_kept_under_their_file_names(tmp_path):
    con = wasde.connect()
    wasde.run(copy_fixtures(tmp_path / "in", MONTHLY, MONTHLY_V2), con)
    got = dict(
        con.execute(
            "SELECT source_file, value FROM raw.wasde WHERE attribute = 'Exports' AND market_year = '2024/25'"
        ).fetchall()
    )
    assert got == {MONTHLY: "2450", MONTHLY_V2: "2475"}


def test_zip_archive_members_are_separate_partitions(tmp_path):
    archive = tmp_path / "oce-wasde-report-data-2010-04-to-2015-12.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("reports/2010-05.csv", fixture_bytes("wasde", "older_layout_2010-05.csv"))
        zf.writestr("reports/2025-01.csv", fixture_bytes("wasde", MONTHLY))
        zf.writestr("reports/readme.txt", "not data")
        zf.writestr("__MACOSX/reports/._2010-05.csv", "resource fork")
    con = wasde.connect()
    res = wasde.run([archive], con)
    assert res.loaded == 2 and count(con) == 31
    assert con.execute("SELECT DISTINCT member FROM raw.wasde ORDER BY 1").fetchall() == [
        ("reports/2010-05.csv",),
        ("reports/2025-01.csv",),
    ]


def test_corrupt_zip_is_logged_and_other_files_still_load(tmp_path):
    bad = tmp_path / "oce-wasde-report-data-2016-01-to-2020-12.zip"
    bad.write_bytes(b"this is not a zip archive")
    files = [bad] + copy_fixtures(tmp_path / "in", MONTHLY)
    con = wasde.connect()
    res = wasde.run(files, con)
    assert res.loaded == 1 and [k for k, _ in res.failures] == [bad.name]
    assert common.report(res, "wasde") == 1


def test_truncated_file_is_logged_and_leaves_the_table_untouched(tmp_path):
    con = wasde.connect()
    good = copy_fixtures(tmp_path / "in", MONTHLY)[0]
    wasde.run([good], con)
    good.write_bytes(fixture_bytes("wasde", "broken_truncated.csv"))  # the file is replaced by a broken copy
    res = wasde.run([good], con)
    assert [k for k, _ in res.failures] == [MONTHLY]
    assert count(con) == 29  # previous load still intact


def test_schema_drift_stops_the_run(tmp_path):
    con = wasde.connect()
    files = copy_fixtures(tmp_path / "in", "broken_missing_value_column.csv", MONTHLY)
    with pytest.raises(common.SchemaDrift):
        wasde.run(files, con)
    assert count(con) == 0


def test_resume_skips_unchanged_files_and_reloads_changed_ones(tmp_path):
    con = wasde.connect()
    path = copy_fixtures(tmp_path / "in", MONTHLY)[0]
    assert wasde.run([path], con, resume=True).loaded == 1
    assert wasde.run([path], con, resume=True).skipped == 1
    path.write_bytes(fixture_bytes("wasde", MONTHLY_V2))  # USDA replaced the file in place
    assert wasde.run([path], con, resume=True).loaded == 1
    assert (
        con.execute("SELECT value FROM raw.wasde WHERE attribute = 'Exports' AND market_year = '2024/25'").fetchone()[0]
        == "2475"
    )


def test_inventory_lists_corn_attributes_and_units(tmp_path):
    con = wasde.connect()
    wasde.run(copy_fixtures(tmp_path / "in", MONTHLY), con)
    inv = wasde.inventory(con)
    assert len(inv) == 14 and set(inv.commodity) == {"Corn"}
    assert inv.loc[inv.attribute == "Avg. Farm Price", "unit"].tolist() == ["$/bu"]


def test_from_dir_command_loads_a_folder(tmp_path):
    copy_fixtures(tmp_path / "manual", MONTHLY, "older_layout_2010-05.csv")
    assert wasde.main(["--from-dir", str(tmp_path / "manual")]) == 0
    con = wasde.connect()
    assert count(con) == 31
