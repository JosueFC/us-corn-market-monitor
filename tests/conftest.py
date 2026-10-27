import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(*parts: str) -> bytes:
    return FIXTURES.joinpath(*parts).read_bytes()


def fixture_json(*parts: str):
    return json.loads(fixture_bytes(*parts))


class FakeResponse:
    def __init__(self, status: int, body: bytes | str = b""):
        self.status_code = status
        self.content = body.encode() if isinstance(body, str) else body
        self.text = self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Replays a scripted list of responses (or exceptions) and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Every test gets its own database and landing zone, and never sleeps."""
    monkeypatch.setenv("CORN_DB", str(tmp_path / "test.duckdb"))
    monkeypatch.setenv("CORN_RAW_DIR", str(tmp_path / "raw"))
    from ingest import common, nass
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)
    monkeypatch.setattr(nass, "PAUSE_SECONDS", 0)
    return tmp_path
