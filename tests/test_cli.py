from __future__ import annotations

import json

from axpcat import __main__ as cli
from axpcat import gate
from axpcat.store import Store
from tests.conftest import EXT_ID, PUBLISHER

ORIGIN_URL = f"https://{PUBLISHER}/agent-extension.json"


def _use(monkeypatch, fake_fetch, tmp_path):
    monkeypatch.setattr(cli.safe_fetch, "fetch", lambda url, max_bytes, timeout: fake_fetch(url, max_bytes))
    monkeypatch.setenv("AXPCAT_DB", str(tmp_path / "catalog.db"))


def test_check_prints_the_package(monkeypatch, fake_fetch, tmp_path, capsys):
    _use(monkeypatch, fake_fetch, tmp_path)
    assert cli.main(["check", ORIGIN_URL]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ext_id"] == EXT_ID and "manifest" not in out


def test_check_reports_a_rejection(monkeypatch, fake_fetch, tmp_path, capsys):
    _use(monkeypatch, fake_fetch, tmp_path)
    assert cli.main(["check", "https://nowhere.example.com/agent-extension.json"]) == 1
    assert "REJECTED" in capsys.readouterr().err


def test_refresh_updates_and_records_failures(monkeypatch, fake_fetch, tmp_path, resign, capsys):
    _use(monkeypatch, fake_fetch, tmp_path)
    store = Store(tmp_path / "catalog.db")
    store.upsert(gate.admit(ORIGIN_URL, fake_fetch))

    def bump(document):
        document["identity"]["version"] = "0.3.5"

    fake_fetch.documents[ORIGIN_URL] = resign(bump)
    assert cli.main(["refresh"]) == 0
    listing = store.get(EXT_ID)
    assert listing is not None and listing.package.version == "0.3.5"

    del fake_fetch.documents[ORIGIN_URL]
    assert cli.main(["refresh"]) == 0
    listing = store.get(EXT_ID)
    assert listing is not None and listing.package.version == "0.3.5"
    assert listing.refresh_error and "HTTP 404" in listing.refresh_error
