import pandas as pd
import pytest
import requests
from conftest import FakeResponse, FakeSession

from ingest import common


def test_redact_removes_secret():
    assert common.redact("GET ...?key=ABC123&year=2024", "ABC123") == "GET ...?key=***&year=2024"
    assert common.redact("nothing to hide", None) == "nothing to hide"


def test_latest_attempt_decides_whether_a_partition_is_loaded():
    con = common.connect()
    common.log_status(con, "nass", "p", "ok", fingerprint="a")
    assert common.is_loaded(con, "nass", "p")
    assert common.is_loaded(con, "nass", "p", fingerprint="a")
    assert not common.is_loaded(con, "nass", "p", fingerprint="b")   # content changed
    common.log_status(con, "nass", "p", "failed", error="boom")
    assert not common.is_loaded(con, "nass", "p")                     # a later failure wins
    assert not common.is_loaded(con, "nass", "never-tried")


@pytest.mark.parametrize("failure", ["bad row", "crash after insert"])
def test_replace_partition_rolls_back_on_error(monkeypatch, failure):
    con = common.connect()
    con.execute("CREATE TABLE raw.t (part VARCHAR, x INTEGER NOT NULL)")
    common.replace_partition(con, "raw.t", {"part": "a"}, pd.DataFrame({"x": [1, 2]}), "test", "a")
    new = pd.DataFrame({"x": [3, None] if failure == "bad row" else [3, 4]})
    if failure == "crash after insert":
        # delete and insert succeed, then the process fails before the transaction is committed
        monkeypatch.setattr(common, "log_status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(Exception):  # noqa: B017 - a constraint error from DuckDB, or the simulated crash
        common.replace_partition(con, "raw.t", {"part": "a"}, new, "test", "a")
    assert sorted(r[0] for r in con.execute("SELECT x FROM raw.t").fetchall()) == [1, 2]
    statuses = [r[0] for r in con.execute("SELECT status FROM raw.ingest_log ORDER BY log_id").fetchall()]
    assert statuses == ["ok"]                                          # the failed attempt left no trace


def test_get_with_retries_backs_off_then_succeeds():
    s = FakeSession(requests.exceptions.ConnectTimeout("slow"), FakeResponse(503), FakeResponse(200, "{}"))
    assert common.get_with_retries(s, "https://example.test", label="x").status_code == 200
    assert len(s.calls) == 3


def test_get_with_retries_gives_up_without_leaking_the_key():
    s = FakeSession(requests.exceptions.ConnectionError("failed for url ?key=SECRETKEY"))
    with pytest.raises(common.IngestError) as e:
        common.get_with_retries(s, "https://example.test", retries=2, label="x", secret="SECRETKEY")
    assert "SECRETKEY" not in str(e.value)


def test_report_exit_code():
    assert common.report(common.RunResult(loaded=1), "nass") == 0
    assert common.report(common.RunResult(failures=[("p", "boom")]), "nass") == 1
