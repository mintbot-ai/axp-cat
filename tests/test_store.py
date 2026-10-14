from __future__ import annotations

from axpcat import gate
from axpcat.store import Store
from tests.conftest import EXT_ID, PUBLISHER

ORIGIN_URL = f"https://{PUBLISHER}/agent-extension.json"


def _store(tmp_path) -> Store:
    return Store(tmp_path / "nested" / "catalog.db")


def test_upsert_get_and_count(tmp_path, fake_fetch):
    store = _store(tmp_path)
    assert store.count() == 0 and store.get(EXT_ID) is None
    listing = store.upsert(gate.admit(ORIGIN_URL, fake_fetch))
    assert listing.package.ext_id == EXT_ID
    assert listing.refresh_error is None
    assert store.count() == 1
    again = store.get(EXT_ID)
    assert again is not None and again.package == listing.package
    assert again.package.manifest["signature"] == listing.package.manifest["signature"]


def test_resubmission_replaces_the_release_and_keeps_listed_at(tmp_path, fake_fetch, resign):
    store = _store(tmp_path)
    first = store.upsert(gate.admit(ORIGIN_URL, fake_fetch))

    def bump(document):
        document["identity"]["version"] = "0.4.0"

    fake_fetch.documents[ORIGIN_URL] = resign(bump)
    second = store.upsert(gate.admit(ORIGIN_URL, fake_fetch))
    assert second.package.version == "0.4.0"
    assert second.listed_at == first.listed_at
    assert store.count() == 1


def test_search_and_publishers(tmp_path, fake_fetch):
    store = _store(tmp_path)
    store.upsert(gate.admit(ORIGIN_URL, fake_fetch))
    assert [l.package.ext_id for l in store.search("knowledge graph")] == [EXT_ID]
    assert store.search("Sample") and store.search("MEMORY")
    assert store.search("toaster") == []
    assert store.search("", publisher=PUBLISHER) and store.search("", publisher="other.example") == []
    assert store.publishers() == [(PUBLISHER, 1)]


def test_refresh_error_is_recorded_and_cleared(tmp_path, fake_fetch):
    store = _store(tmp_path)
    package = gate.admit(ORIGIN_URL, fake_fetch)
    store.upsert(package)
    store.mark_refresh_error(EXT_ID, "origin went away")
    listing = store.get(EXT_ID)
    assert listing is not None and listing.refresh_error == "origin went away"
    assert store.upsert(package).refresh_error is None


def test_delete(tmp_path, fake_fetch):
    store = _store(tmp_path)
    store.upsert(gate.admit(ORIGIN_URL, fake_fetch))
    assert store.delete(EXT_ID) is True
    assert store.delete(EXT_ID) is False
    assert store.count() == 0
