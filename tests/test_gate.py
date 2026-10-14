from __future__ import annotations

import json

import pytest
from axp import repository

from axpcat import gate
from tests.conftest import EXT_ID, PUBLISHER, FakeFetch

ORIGIN_URL = f"https://{PUBLISHER}/agent-extension.json"
GITHUB_URL = "https://github.com/example-org/sample-memory"
KEYDIR_URL = f"https://{PUBLISHER}/.well-known/agent-extension-keys.json"


def test_manifest_served_from_the_publisher_origin_is_listed(fake_fetch):
    package = gate.admit(ORIGIN_URL, fake_fetch)
    assert package.ext_id == EXT_ID
    assert package.version == "0.3.0"
    assert package.binding == gate.BINDING_ORIGIN
    assert package.source == gate.SOURCE_MANIFEST_URL
    assert package.manifest_url == ORIGIN_URL
    assert "posix" in package.runtimes and "hermes" in package.runtimes
    assert "mcp_servers" in package.provides
    assert package.permissions["root"] is True
    # The origin bound the key; the key directory was never needed.
    assert KEYDIR_URL not in fake_fetch.calls


def test_bare_domain_resolves_through_well_known(fake_fetch):
    package = gate.admit("ext.example.com", fake_fetch)
    assert package.locator == "https://ext.example.com"
    assert package.source == gate.SOURCE_WELL_KNOWN
    assert package.manifest_url == f"https://{PUBLISHER}/.well-known/agent-extension.json"


def test_landing_page_link_rel_wins_over_well_known(fake_fetch, manifest_bytes):
    fake_fetch.documents[f"https://{PUBLISHER}/"] = (
        b"<html><head><link rel='agent-extension' href='/ext/manifest.json'></head></html>"
    )
    fake_fetch.documents[f"https://{PUBLISHER}/ext/manifest.json"] = manifest_bytes
    package = gate.admit(f"https://{PUBLISHER}/", fake_fetch)
    assert package.source == gate.SOURCE_LINK_REL
    assert package.manifest_url == f"https://{PUBLISHER}/ext/manifest.json"


def test_github_release_is_listed_through_the_key_directory(fake_fetch):
    package = gate.admit(GITHUB_URL, fake_fetch)
    assert package.binding == gate.BINDING_KEY_DIRECTORY
    assert package.source == repository.SOURCE_RELEASE_LATEST
    assert KEYDIR_URL in fake_fetch.calls


def test_github_release_without_a_key_directory_is_rejected(fake_fetch):
    del fake_fetch.documents[KEYDIR_URL]
    with pytest.raises(gate.Rejected, match="nothing binds the signing key"):
        gate.admit(GITHUB_URL, fake_fetch)


def test_key_directory_that_lists_another_key_is_rejected(fake_fetch):
    fake_fetch.documents[KEYDIR_URL] = json.dumps({
        "publisher": PUBLISHER,
        "keys": [{"public_key": "ed25519:" + "A" * 43 + "=", "key_id": "other"}],
    }).encode()
    with pytest.raises(gate.Rejected, match="does not list the key"):
        gate.admit(GITHUB_URL, fake_fetch)


def test_key_directory_scoped_to_another_extension_is_rejected(fake_fetch, key_directory_bytes):
    directory = json.loads(key_directory_bytes)
    directory["keys"][0]["extensions"] = ["something-else"]
    fake_fetch.documents[KEYDIR_URL] = json.dumps(directory).encode()
    with pytest.raises(gate.Rejected, match="does not list the key"):
        gate.admit(GITHUB_URL, fake_fetch)


def test_unsigned_manifest_is_rejected(fake_fetch, manifest):
    unsigned = dict(manifest)
    unsigned.pop("signature")
    unsigned.pop("signing")  # a signing block without a signature is a validation error instead
    fake_fetch.documents[ORIGIN_URL] = json.dumps(unsigned).encode()
    with pytest.raises(gate.Rejected, match="unsigned"):
        gate.admit(ORIGIN_URL, fake_fetch)


def test_tampered_manifest_is_rejected(fake_fetch, manifest):
    tampered = json.loads(json.dumps(manifest))
    tampered["identity"]["description"] = "now with a free toaster"
    fake_fetch.documents[ORIGIN_URL] = json.dumps(tampered).encode()
    with pytest.raises(gate.Rejected, match="does not verify"):
        gate.admit(ORIGIN_URL, fake_fetch)


def test_structurally_invalid_manifest_is_rejected_before_signing(fake_fetch, manifest):
    broken = json.loads(json.dumps(manifest))
    broken["targets"] = []
    fake_fetch.documents[ORIGIN_URL] = json.dumps(broken).encode()
    with pytest.raises(gate.Rejected, match="not a valid AXP manifest: targets"):
        gate.admit(ORIGIN_URL, fake_fetch)


def test_not_json_is_rejected(fake_fetch):
    fake_fetch.documents[ORIGIN_URL] = b"<html>hello</html>"
    with pytest.raises(gate.Rejected, match="not valid JSON"):
        gate.admit(ORIGIN_URL, fake_fetch)


def test_expired_release_is_rejected(fake_fetch, resign):
    def expire(document):
        document["release"]["valid_until"] = "2020-01-01T00:00:00Z"

    fake_fetch.documents[ORIGIN_URL] = resign(expire)
    with pytest.raises(gate.Rejected, match="valid_until"):
        gate.admit(ORIGIN_URL, fake_fetch)


def test_resigned_manifest_with_a_valid_change_is_listed(fake_fetch, resign):
    def bump(document):
        document["identity"]["version"] = "0.3.1"

    fake_fetch.documents[ORIGIN_URL] = resign(bump)
    assert gate.admit(ORIGIN_URL, fake_fetch).version == "0.3.1"


def test_subdomain_of_the_publisher_counts_as_origin(fake_fetch, manifest_bytes):
    url = f"https://cdn.{PUBLISHER}/agent-extension.json"
    fake_fetch.documents[url] = manifest_bytes
    assert gate.admit(url, fake_fetch).binding == gate.BINDING_ORIGIN


def test_lookalike_domain_is_not_the_origin(fake_fetch, manifest_bytes):
    url = "https://notext.example.com/agent-extension.json"
    fake_fetch.documents[url] = manifest_bytes
    del fake_fetch.documents[KEYDIR_URL]
    with pytest.raises(gate.Rejected, match="nothing binds"):
        gate.admit(url, fake_fetch)


@pytest.mark.parametrize("raw", ["", "   ", "http://ext.example.com/agent-extension.json",
                                 "ftp://ext.example.com/x.json", "https://user:pw@ext.example.com/x.json",
                                 "https://" + "a" * 2100])
def test_bad_locators_are_rejected(raw):
    with pytest.raises(gate.Rejected):
        gate.normalize_locator(raw)


def test_absent_manifest_is_a_clear_rejection():
    with pytest.raises(gate.Rejected, match="HTTP 404"):
        gate.admit("https://nowhere.example.com/agent-extension.json", FakeFetch())


def test_package_as_dict_round_trips_lists(fake_fetch):
    data = gate.admit(ORIGIN_URL, fake_fetch).as_dict()
    assert isinstance(data["runtimes"], list) and isinstance(data["keywords"], list)
    assert data["manifest"]["kind"] == "agent-extension"
