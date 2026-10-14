"""Shared fixtures: the spec's worked example (ext.example.com/sample-memory,
signed with the deliberately public example key) and an in-memory fake of
the ``Fetch`` contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axp import signing
from axp.repository import Absent

FIXTURES = Path(__file__).parent / "fixtures"
PUBLISHER = "ext.example.com"
EXT_ID = "ext.example.com/sample-memory"


@pytest.fixture(scope="session")
def manifest_bytes() -> bytes:
    return (FIXTURES / "agent-extension.json").read_bytes()


@pytest.fixture(scope="session")
def manifest(manifest_bytes) -> dict:
    return json.loads(manifest_bytes)


@pytest.fixture(scope="session")
def key_directory_bytes() -> bytes:
    return (FIXTURES / "agent-extension-keys.json").read_bytes()


@pytest.fixture(scope="session")
def signing_key() -> bytes:
    return (FIXTURES / "example-signing.key").read_bytes()


@pytest.fixture
def resign(manifest, signing_key):
    """A copy of the example manifest with ``mutate`` applied and a fresh signature."""

    def _resign(mutate) -> bytes:
        document = json.loads(json.dumps(manifest))
        mutate(document)
        return json.dumps(signing.sign_manifest(document, signing_key)).encode()

    return _resign


class FakeFetch:
    """``fetch(url, max_bytes)`` backed by a dict; unknown URLs are Absent."""

    def __init__(self, documents: dict[str, bytes] | None = None) -> None:
        self.documents = dict(documents or {})
        self.calls: list[str] = []

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.calls.append(url)
        if url not in self.documents:
            raise Absent(f"{url} returned HTTP 404")
        return self.documents[url]


@pytest.fixture
def fake_fetch(manifest_bytes, key_directory_bytes) -> FakeFetch:
    """The publisher serves its manifest at the origin and on GitHub, and
    publishes a key directory - every binding path is available."""
    return FakeFetch({
        f"https://{PUBLISHER}/agent-extension.json": manifest_bytes,
        f"https://{PUBLISHER}/.well-known/agent-extension.json": manifest_bytes,
        f"https://{PUBLISHER}/.well-known/agent-extension-keys.json": key_directory_bytes,
        "https://github.com/example-org/sample-memory/releases/latest/download/agent-extension.json": manifest_bytes,
    })
