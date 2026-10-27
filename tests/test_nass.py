import datetime as dt
import json

import pytest
from conftest import FakeResponse, FakeSession, fixture_bytes, fixture_json

from ingest import common, nass

KEY = "TEST-KEY-123"
QUERIES = nass.load_queries()
PRICE = QUERIES["price_received_national"]["params"]
NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)


def ok(name="price_received_national_2024.json"):
    return FakeResponse(200, fixture_bytes("nass", name))


def rows(con, where=""):
    return con.execute(f"SELECT count(*) FROM raw.nass_quickstats {where}").fetchone()[0]


# ----------------------------------------------------------------- configuration


def test_shipped_config_is_valid():
    assert {"price_received_national", "annual_state", "stocks_national"} <= set(QUERIES)


@pytest.mark.parametrize(
    "body, message",
    [
        ("queries: {}", "non-empty"),
        ("queries:\n  Bad-Name:\n    start_year: 2000\n    params: {commodity_desc: CORN}", "snake_case"),
        ("queries:\n  q:\n    start_year: 2000\n    params: {year: '2020'}", "set by the loader"),
        ("queries:\n  q:\n    start_year: 2000\n    params: {commodity_desc: ''}", "non-empty string"),
        ("queries:\n  q:\n    start_year: 1700\n    params: {commodity_desc: CORN}", "start_year"),
    ],
)
def test_bad_config_is_rejected(tmp_path, body, message):
    p = tmp_path / "q.yml"
    p.write_text(body)
    with pytest.raises(ValueError, match=message):
        nass.load_queries(p)


def test_missing_api_key_exits_with_instructions(monkeypatch):
    monkeypatch.delenv("NASS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="NASS_API_KEY"):
        nass._api_key()


# ----------------------------------------------------------------- API responses


def test_fetch_sends_filters_year_and_json_format():
    s = FakeSession(ok())
    records = nass.fetch(PRICE, 2024, KEY, s)
    assert len(records) == 13
    url, params = s.calls[0]
    assert url.endswith("/api_GET/")
    assert params["year"] == "2024" and params["format"] == "JSON" and params["key"] == KEY
    assert params["commodity_desc"] == "CORN"


def test_row_limit_error_is_not_loaded():
    with pytest.raises(nass.TooManyRows):
        nass.fetch(PRICE, 2024, KEY, FakeSession(FakeResponse(400, fixture_bytes("nass", "error_exceeds_limit.json"))))


def test_result_at_the_row_limit_is_treated_as_truncated():
    record = fixture_json("nass", "price_received_national_2024.json")["data"][0]
    body = json.dumps({"data": [record] * nass.ROW_LIMIT})
    with pytest.raises(nass.TooManyRows):
        nass.fetch(PRICE, 2024, KEY, FakeSession(FakeResponse(200, body)))


def test_bad_request_means_no_data():
    with pytest.raises(nass.NoData):
        nass.fetch(PRICE, 2099, KEY, FakeSession(FakeResponse(400, fixture_bytes("nass", "error_bad_request.json"))))


def test_rejected_key_is_an_auth_error():
    with pytest.raises(nass.AuthError):
        nass.fetch(PRICE, 2024, KEY, FakeSession(FakeResponse(401, fixture_bytes("nass", "error_unauthorized.json"))))


def test_error_messages_never_contain_the_key():
    echo = FakeResponse(502, json.dumps({"error": [f"server error for key={KEY}"]}))  # retried, then:
    final = FakeResponse(418, json.dumps({"error": [f"teapot for key={KEY}"]}))
    with pytest.raises(common.IngestError) as e:
        nass.fetch(PRICE, 2024, KEY, FakeSession(echo, final))
    assert KEY not in str(e.value)


def test_missing_data_list_is_schema_drift():
    with pytest.raises(common.SchemaDrift):
        nass.fetch(PRICE, 2024, KEY, FakeSession(ok("broken_no_data_key.json")))


# ----------------------------------------------------------------- parsing and loading


def test_missing_value_field_is_schema_drift_and_table_is_untouched():
    con = nass.connect()
    good = fixture_json("nass", "price_received_national_2024.json")["data"]
    nass.load_partition(con, "price_received_national", 2024, good, NOW)
    broken = fixture_json("nass", "broken_missing_value_field.json")["data"]
    with pytest.raises(common.SchemaDrift, match="Value"):
        nass.load_partition(con, "price_received_national", 2024, broken, NOW)
    assert rows(con) == 13


def test_values_are_stored_exactly_as_served():
    con = nass.connect()
    nass.load_partition(con, "annual_state", 2024, fixture_json("nass", "annual_state_2024.json")["data"], NOW)
    got = dict(
        con.execute(
            "SELECT state_alpha || ' ' || reference_period_desc || ' ' || statisticcat_desc, value "
            "FROM raw.nass_quickstats"
        ).fetchall()
    )
    assert got["IA YEAR PRODUCTION"] == "2,550,000,000"
    assert got["RI YEAR PRODUCTION"] == "(D)"
    assert got["IA YEAR - AUG FORECAST YIELD"] == "214"  # in-season forecasts are kept
    assert con.execute("SELECT DISTINCT year FROM raw.nass_quickstats").fetchall() == [("2024",)]


def test_optional_field_missing_loads_as_null():
    records = [
        {k: v for k, v in r.items() if k != "CV (%)"}
        for r in fixture_json("nass", "price_received_national_2024.json")["data"]
    ]
    con = nass.connect()
    nass.load_partition(con, "price_received_national", 2024, records, NOW)
    assert rows(con, "WHERE cv_pct IS NULL") == 13


def test_reload_is_idempotent_and_replaces_revised_values():
    con = nass.connect()
    data = fixture_json("nass", "price_received_national_2024.json")["data"]
    nass.load_partition(con, "price_received_national", 2024, data, NOW)
    nass.load_partition(con, "price_received_national", 2024, data, NOW)
    assert rows(con) == 13
    revised = [dict(r, Value="9.99") if r["reference_period_desc"] == "DEC" else r for r in data][:12]
    nass.load_partition(con, "price_received_national", 2024, revised, NOW)
    assert rows(con) == 12
    dec = con.execute("SELECT value FROM raw.nass_quickstats WHERE reference_period_desc = 'DEC'").fetchone()[0]
    assert dec == "9.99"


def test_landing_zone_snapshot_is_written_without_the_key(isolated_paths):
    con = nass.connect()
    nass.run(QUERIES, {"price_received_national": [2024]}, KEY, con, session=FakeSession(ok()))
    files = list((isolated_paths / "raw" / "nass" / "price_received_national" / "year=2024").glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text()
    assert KEY not in text
    assert len(json.loads(text)["data"]) == 13


# ----------------------------------------------------------------- runs, failure log and resume


def _scripted_fetch(monkeypatch, behaviour):
    calls = []

    def fake(params, year, api_key, session=None, label=""):
        calls.append(label)
        outcome = behaviour(label, calls.count(label))
        if isinstance(outcome, Exception):
            raise outcome
        return fixture_json("nass", "price_received_national_2024.json")["data"]

    monkeypatch.setattr(nass, "fetch", fake)
    return calls


def test_failed_partition_is_logged_and_the_run_continues(monkeypatch):
    con = nass.connect()
    _scripted_fetch(
        monkeypatch,
        lambda label, n: RuntimeError("HTTP 500 boom") if label == "price_received_national/year=2021" else None,
    )
    res = nass.run(QUERIES, {"price_received_national": [2020, 2021, 2022]}, KEY, con)
    assert res.loaded == 2 and res.rows == 26
    assert [k for k, _ in res.failures] == ["price_received_national/year=2021"]
    err = con.execute("SELECT error FROM raw.ingest_log WHERE status = 'failed'").fetchone()[0]
    assert "boom" in err
    assert common.report(res, "nass") == 1


def test_resume_retries_failures_skips_old_partitions_and_refreshes_recent_years(monkeypatch):
    con = nass.connect()
    this_year = dt.date.today().year
    years = list(range(this_year - 4, this_year + 1))
    first_failure = f"price_received_national/year={years[0]}"
    calls = _scripted_fetch(
        monkeypatch, lambda label, n: RuntimeError("transient") if label == first_failure and n == 1 else None
    )
    nass.run(QUERIES, {"price_received_national": years}, KEY, con, resume=True)
    before = len(calls)
    second = nass.run(QUERIES, {"price_received_national": years}, KEY, con, resume=True, refresh_years=2)
    assert second.failures == []
    # the failed year is retried, the two most recent years are refreshed, the rest are skipped
    assert calls[before:] == [
        first_failure,
        f"price_received_national/year={this_year - 1}",
        f"price_received_national/year={this_year}",
    ]
    assert second.skipped == 2


def test_schema_drift_stops_the_run_and_is_logged(monkeypatch):
    con = nass.connect()
    _scripted_fetch(monkeypatch, lambda label, n: common.SchemaDrift("Value missing"))
    with pytest.raises(common.SchemaDrift):
        nass.run(QUERIES, {"price_received_national": [2020, 2021]}, KEY, con)
    assert con.execute("SELECT count(*) FROM raw.ingest_log WHERE status = 'failed'").fetchone()[0] == 1


def test_a_query_with_no_rows_in_any_year_is_reported_as_failed(monkeypatch):
    con = nass.connect()
    _scripted_fetch(monkeypatch, lambda label, n: nass.NoData("no rows"))
    res = nass.run(QUERIES, {"price_received_national": [2020, 2021, 2022]}, KEY, con)
    assert len(res.no_data) == 3
    assert res.failures and "check the filters" in res.failures[0][1]


def test_an_unpublished_latest_year_is_not_a_failure(monkeypatch):
    con = nass.connect()
    _scripted_fetch(monkeypatch, lambda label, n: nass.NoData("no rows") if label.endswith("2026") else None)
    res = nass.run(QUERIES, {"price_received_national": [2025, 2026]}, KEY, con)
    assert res.failures == [] and res.no_data == ["price_received_national/year=2026"]


# ----------------------------------------------------------------- --check-config


def _check_config_session(param_values: dict, counts: int):
    class S(FakeSession):
        def get(self, url, params=None, **kw):
            self.calls.append((url, dict(params or {})))
            if url.endswith("/get_param_values/"):
                p = params["param"]
                return FakeResponse(200, json.dumps({p: param_values.get(p, [])}))
            return FakeResponse(200, json.dumps({"count": counts}))

    return S(None)


def test_check_config_passes_when_values_exist_and_queries_return_rows(capsys):
    queries = {"q": {"start_year": 2000, "params": {"statisticcat_desc": "PRICE RECEIVED"}}}
    values = fixture_json("nass", "get_param_values_statisticcat_desc.json")
    assert nass.check_config(queries, KEY, 2025, _check_config_session(values, 13)) == 0
    assert "Configuration OK" in capsys.readouterr().out


def test_check_config_flags_unknown_values_with_suggestions(capsys):
    queries = {"q": {"start_year": 2000, "params": {"statisticcat_desc": "PRICE RECIEVED"}}}  # misspelled
    values = fixture_json("nass", "get_param_values_statisticcat_desc.json")
    assert nass.check_config(queries, KEY, 2025, _check_config_session(values, 13)) == 1
    out = capsys.readouterr().out
    assert "UNKNOWN  statisticcat_desc = PRICE RECIEVED (closest: PRICE RECEIVED" in out


def test_check_config_flags_a_query_that_returns_nothing(capsys):
    queries = {"q": {"start_year": 2000, "params": {"statisticcat_desc": "STOCKS"}}}
    values = fixture_json("nass", "get_param_values_statisticcat_desc.json")
    assert nass.check_config(queries, KEY, 2025, _check_config_session(values, 0)) == 1
    assert "ERROR q" in capsys.readouterr().out


def test_check_config_reports_a_rejected_key_without_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("NASS_API_KEY", KEY)
    monkeypatch.setattr(
        nass.requests,
        "Session",
        lambda: FakeSession(FakeResponse(401, fixture_bytes("nass", "error_unauthorized.json"))),
    )
    assert nass.main(["--check-config"]) == 2
    out = capsys.readouterr().out
    assert "STOPPED" in out and "rejected the key" in out and KEY not in out


@pytest.mark.parametrize("value", ["<your key>", "<key"])
def test_placeholder_key_is_rejected_before_any_request(monkeypatch, value):
    monkeypatch.setenv("NASS_API_KEY", value)
    with pytest.raises(SystemExit, match="placeholder"):
        nass._api_key()


HTML_403 = "<html><head><title>403 Forbidden</title></head><body>Microsoft-Azure-Application-Gateway/v2</body></html>"


def test_gateway_block_is_retried_after_a_wait_and_is_not_an_auth_error():
    s = FakeSession(FakeResponse(403, HTML_403), ok())
    assert len(nass.fetch(PRICE, 2024, KEY, s)) == 13
    assert len(s.calls) == 2


def test_persistent_gateway_block_stops_the_run_with_resume_advice(monkeypatch):
    con = nass.connect()
    monkeypatch.setattr(nass.requests, "Session", lambda: FakeSession(FakeResponse(403, HTML_403)))
    with pytest.raises(nass.GatewayBlocked, match="--resume"):
        nass.run(QUERIES, {"price_received_national": [2020, 2021]}, KEY, con)
    assert con.execute("SELECT count(*) FROM raw.ingest_log WHERE status = 'failed'").fetchone()[0] == 1


def test_json_403_is_still_an_auth_error():
    with pytest.raises(nass.AuthError):
        nass.fetch(PRICE, 2024, KEY, FakeSession(FakeResponse(403, json.dumps({"error": ["unauthorized"]}))))
