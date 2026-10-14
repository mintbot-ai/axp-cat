from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from axpcat import config
from axpcat.app import SubmitLimiter, create_app
from axpcat.store import Store
from tests.conftest import EXT_ID, PUBLISHER

ORIGIN_URL = f"https://{PUBLISHER}/agent-extension.json"


@pytest.fixture
def client(tmp_path, fake_fetch):
    settings = config.Settings(public_base="https://test.axp.cat", db_path=tmp_path / "catalog.db", submit_per_hour=2)
    app = create_app(settings=settings, store=Store(settings.db_path), fetch=fake_fetch)
    return TestClient(app)


def test_index_and_empty_catalog(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "Agent Extension Protocol" in index.text
    assert index.headers["content-security-policy"].startswith("default-src 'self'")
    assert index.headers["x-content-type-options"] == "nosniff"
    catalog = client.get("/catalog")
    assert catalog.status_code == 200 and "brand new" in catalog.text
    assert client.get("/api/catalog.json").json() == {"count": 0, "packages": []}
    assert client.get("/health").json()["packages"] == 0
    assert "Disallow: /submit" in client.get("/robots.txt").text


def test_submit_lists_a_valid_package(client):
    response = client.post("/submit", data={"locator": ORIGIN_URL}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/catalog/{EXT_ID}"

    page = client.get(f"/catalog/{EXT_ID}")
    assert page.status_code == 200
    assert "Sample Memory" in page.text
    assert "install ext.example.com/agent-extension.json" in page.text
    assert "registry-1.docker.io:443" in page.text  # declared egress is shown
    assert "installs a systemd unit" in page.text  # with its reason

    listing = client.get(f"/api/catalog/{EXT_ID}.json").json()
    assert listing["version"] == "0.3.0" and listing["binding"] == "origin"
    assert client.get("/api/catalog.json").json()["count"] == 1
    assert "Sample Memory" in client.get("/").text  # shows up under "recently listed"
    assert "Sample Memory" in client.get("/catalog?q=graph").text
    assert "Nothing matches" in client.get("/catalog?q=toaster").text


def test_submit_rejection_shows_the_reason(client, fake_fetch, manifest):
    import json

    unsigned = dict(manifest)
    unsigned.pop("signature")
    unsigned.pop("signing")
    fake_fetch.documents[ORIGIN_URL] = json.dumps(unsigned).encode()
    response = client.post("/submit", data={"locator": ORIGIN_URL})
    assert response.status_code == 422
    assert "Not listed." in response.text and "unsigned" in response.text
    assert client.get("/api/catalog.json").json()["count"] == 0


def test_submit_is_rate_limited_per_address(client):
    headers = {"X-Real-IP": "203.0.113.7"}
    for _ in range(2):
        assert client.post("/submit", data={"locator": ORIGIN_URL}, headers=headers, follow_redirects=False).status_code == 303
    assert client.post("/submit", data={"locator": ORIGIN_URL}, headers=headers).status_code == 429
    # Another address is unaffected.
    other = {"X-Real-IP": "203.0.113.8"}
    assert client.post("/submit", data={"locator": ORIGIN_URL}, headers=other, follow_redirects=False).status_code == 303


def test_unknown_package_is_404(client):
    response = client.get("/catalog/nobody.example/nothing")
    assert response.status_code == 404 and "nobody.example/nothing" in response.text
    assert client.get("/api/catalog/nobody.example/nothing.json").status_code == 404


def test_hostile_strings_are_escaped(client, fake_fetch, resign):
    def poison(document):
        document["identity"]["description"] = "<script>alert(1)</script>"
        document["permissions"]["secrets"] = ["<img src=x onerror=alert(2)>"]

    fake_fetch.documents[ORIGIN_URL] = resign(poison)
    assert client.post("/submit", data={"locator": ORIGIN_URL}, follow_redirects=False).status_code == 303
    page = client.get(f"/catalog/{EXT_ID}").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page


def test_submit_limiter_window():
    limiter = SubmitLimiter(per_hour=1)
    assert limiter.allow("a", now=0.0)
    assert not limiter.allow("a", now=10.0)
    assert limiter.allow("a", now=3601.0)
