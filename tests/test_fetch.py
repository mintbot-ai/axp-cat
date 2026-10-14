from __future__ import annotations

import socket

import pytest

from axpcat import fetch


def _resolves_to(monkeypatch, *addresses: str):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.9", "169.254.169.254",
                                     "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fd00::1", "224.0.0.1"])
def test_private_and_special_addresses_are_refused(monkeypatch, address):
    _resolves_to(monkeypatch, address)
    with pytest.raises(fetch.FetchError, match="not a public address"):
        fetch.public_address("host.example", 443)


def test_a_name_mixing_public_and_private_answers_is_refused(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34", "10.0.0.1")
    with pytest.raises(fetch.FetchError, match="not a public address"):
        fetch.public_address("host.example", 443)


def test_public_address_is_returned(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert fetch.public_address("host.example", 443) == "93.184.216.34"


def test_unresolvable_name(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(fetch.FetchError, match="DNS lookup failed"):
        fetch.public_address("host.example", 443)


@pytest.mark.parametrize("url", ["http://example.com/x.json", "https://user:pw@example.com/", "https:///nohost", "file:///etc/passwd"])
def test_only_plain_https_urls_are_fetched(url):
    with pytest.raises(fetch.FetchError):
        fetch.fetch(url, 1024)


def test_fetch_never_connects_to_a_private_address(monkeypatch):
    _resolves_to(monkeypatch, "10.0.0.1")
    connected = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connected.append(a))
    with pytest.raises(fetch.FetchError):
        fetch.fetch("https://internal.example/agent-extension.json", 1024)
    assert connected == []
