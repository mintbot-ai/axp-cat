"""HTTPS fetch for visitor-supplied URLs.

The catalog fetches whatever a visitor submits, so this is the one place where
"a URL from the internet" turns into bytes. Rules:

* https only, no credentials in the URL;
* the hostname is resolved first and every answer must be a public unicast
  address (no loopback, private, link-local, shared, reserved or multicast
  ranges); the request then goes to that pinned address while the hostname
  is kept for SNI and the Host header, so a DNS answer that flips between
  the check and the connect buys an attacker nothing;
* redirects are followed (GitHub release assets redirect to
  objects.githubusercontent.com) and every hop passes the same checks;
* the body is capped at ``max_bytes`` - larger is an error, never a
  truncation;
* HTTP 404 / 410 raise :class:`axp.repository.Absent` so the repository walk
  can move on; every other failure raises :class:`FetchError`.

``fetch`` honours the ``axp.repository.Fetch`` contract:
``fetch(url, max_bytes) -> bytes``.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse

from axp.repository import Absent, RepositoryError

MAX_REDIRECTS = 5
USER_AGENT = "axp.cat/1 (+https://axp.cat)"
ACCEPT = "application/json, application/octet-stream;q=0.9, text/html;q=0.8, */*;q=0.1"
REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class FetchError(RepositoryError):
    """The URL could not be fetched safely. The message is user-facing."""


def check_url(url: str) -> urllib.parse.SplitResult:
    """The parts of ``url`` when it is a fetchable https URL; FetchError otherwise."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise FetchError(f"{url}: only https:// URLs are fetched")
    if parts.username or parts.password:
        raise FetchError(f"{url}: credentials in the URL are not allowed")
    return parts


def public_address(hostname: str, port: int) -> str:
    """One address ``hostname`` resolves to, after checking that every
    address it resolves to is public. A name that mixes public and private
    answers is refused outright - that is the DNS-rebinding shape."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"{hostname}: DNS lookup failed ({exc.strerror or exc})") from exc
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global or address.is_multicast:
            raise FetchError(f"{hostname} resolves to {address}, which is not a public address")
        addresses.append(str(address))
    if not addresses:
        raise FetchError(f"{hostname}: no address")
    return addresses[0]


class _PinnedConnection(http.client.HTTPSConnection):
    """TLS to a pre-checked address while certificate validation and SNI
    still use the hostname."""

    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:  # pragma: no cover - exercised only against the network
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch(url: str, max_bytes: int, *, timeout: float = 15.0) -> bytes:
    """GET ``url`` safely (see the module docstring) and return its body."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        parts = check_url(current)
        assert parts.hostname is not None
        port = parts.port or 443
        address = public_address(parts.hostname, port)
        target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        connection = _PinnedConnection(parts.hostname, address, port, timeout)
        try:
            connection.request("GET", target, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
            response = connection.getresponse()
            status = response.status
            if status in REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location:
                    raise FetchError(f"{current}: redirect without a Location header")
                current = urllib.parse.urljoin(current, location)
                continue
            if status in (404, 410):
                raise Absent(f"{current} returned HTTP {status}")
            if status != 200:
                raise FetchError(f"{current} returned HTTP {status}")
            body = response.read(max_bytes + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f"{current}: {exc}") from exc
        finally:
            connection.close()
        if len(body) > max_bytes:
            raise FetchError(f"{current} exceeds {max_bytes} bytes")
        return body
    raise FetchError(f"{url}: more than {MAX_REDIRECTS} redirects")
