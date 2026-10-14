"""The admission gate: what a submission must prove before it is listed.

Listing is open to anyone, so this gate is the catalog's whole integrity
story. A submission is a *locator* - a manifest URL, a publisher domain or a
GitHub repository URL - and it is listed only when ALL of these hold:

1. The locator resolves to an AXP manifest the way a host resolves it
   (SPEC 3.1): a ``.json`` URL is the manifest; a page or bare domain is
   searched for ``<link rel="agent-extension">`` and then
   ``/.well-known/agent-extension.json``; a repository URL is walked with
   ``axp.repository.resolve`` (newest release asset first, root file last).
2. The ``axp`` reference implementation validates the manifest in full -
   every target, the forward-compatibility rules of SPEC 2.4 included.
3. The manifest is signed and the signature verifies against the key it
   carries (``signing.public_key``).
4. That key is bound to ``identity.publisher`` through a channel the
   publisher controls: the manifest was served from the publisher's own
   domain (or a subdomain of it, SPEC 9), or the publisher's key directory
   at ``https://<publisher>/.well-known/agent-extension-keys.json`` lists
   the key for this extension (SPEC 8.5). A repository is never anybody's
   origin, so a GitHub-hosted manifest is listed through the key directory
   only.
5. The release is not past its ``valid_until`` date.

Nothing here judges quality or safety. A listed package is a well-formed,
signed, publisher-bound description; what a host does with it is the host's
own trust decision (TOFU pinning, consent, sandboxing).
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

from axp import manifest as axp_manifest
from axp import repository, signing, updates

MAX_MANIFEST_BYTES = repository.MAX_MANIFEST_BYTES
MAX_PAGE_BYTES = 512 * 1024
MAX_KEY_DIRECTORY_BYTES = 64 * 1024
MAX_LOCATOR_LENGTH = 2048
WELL_KNOWN_PATH = "/.well-known/agent-extension.json"

# How the manifest was found (``Package.source``). Repository locators carry
# the ``axp.repository.SOURCE_*`` values instead.
SOURCE_MANIFEST_URL = "manifest-url"
SOURCE_LINK_REL = "link-rel"
SOURCE_WELL_KNOWN = "well-known"

# How the signing key is tied to the publisher (``Package.binding``).
BINDING_ORIGIN = "origin"
BINDING_KEY_DIRECTORY = "key-directory"


class Rejected(ValueError):
    """The submission does not meet the listing requirements. User-facing."""


@dataclass(frozen=True)
class Package:
    """A listed package - the catalog's one record type."""

    ext_id: str
    publisher: str
    name: str
    version: str
    display_name: str
    description: str
    homepage: str | None
    license: str | None
    keywords: tuple[str, ...]
    channel: str
    runtimes: tuple[str, ...]
    provides: tuple[str, ...]
    permissions: dict
    public_key: str
    key_id: str | None
    locator: str
    manifest_url: str
    source: str
    binding: str
    published_at: str | None
    valid_until: str | None
    manifest: dict

    def as_dict(self) -> dict:
        data = asdict(self)
        data["keywords"] = list(self.keywords)
        data["runtimes"] = list(self.runtimes)
        data["provides"] = list(self.provides)
        return data


# ---------------------------------------------------------------------------
# Locators
# ---------------------------------------------------------------------------

def normalize_locator(raw: str) -> str:
    """The https locator a submission names, or Rejected."""
    locator = (raw or "").strip()
    if not locator:
        raise Rejected("enter a manifest URL, a publisher domain or a GitHub repository URL")
    if len(locator) > MAX_LOCATOR_LENGTH:
        raise Rejected(f"the locator is longer than {MAX_LOCATOR_LENGTH} characters")
    if "://" not in locator:
        locator = "https://" + locator
    parts = urllib.parse.urlsplit(locator)
    if parts.scheme != "https" or not parts.hostname:
        raise Rejected("only https:// locators are accepted (SPEC 3.1: fetches must be HTTPS)")
    if parts.username or parts.password:
        raise Rejected("credentials in the locator are not allowed")
    return locator


class _LinkRelParser(HTMLParser):
    """Collects ``<link rel="agent-extension" href="...">`` from a landing page."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attributes = dict(attrs)
        rel = (attributes.get("rel") or "").lower().split()
        href = attributes.get("href")
        if "agent-extension" in rel and href:
            self.hrefs.append(href)


def link_rel_manifest(page: bytes, base_url: str) -> str | None:
    """The manifest URL a landing page advertises, if any."""
    parser = _LinkRelParser()
    try:
        parser.feed(page.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - a broken page simply advertises nothing
        return None
    return urllib.parse.urljoin(base_url, parser.hrefs[0]) if parser.hrefs else None


def _fetch_manifest(url: str, fetch: repository.Fetch) -> dict:
    try:
        body = fetch(url, MAX_MANIFEST_BYTES)
        return repository.parse_manifest(body, url)
    except repository.RepositoryError as exc:
        raise Rejected(str(exc)) from exc


def discover(locator: str, fetch: repository.Fetch) -> tuple[dict, str, str]:
    """``(document, manifest_url, source)`` for a locator, resolved as a host
    would (SPEC 3.1)."""
    if repository.is_repository_locator(locator):
        try:
            resolution = repository.resolve(locator, fetch)
        except repository.RepositoryError as exc:
            raise Rejected(str(exc)) from exc
        return resolution.manifest, resolution.manifest_url, resolution.source

    parts = urllib.parse.urlsplit(locator)
    if parts.path.endswith(".json"):
        return _fetch_manifest(locator, fetch), locator, SOURCE_MANIFEST_URL

    try:
        page = fetch(locator, MAX_PAGE_BYTES)
    except repository.Absent:
        page = b""
    except repository.RepositoryError as exc:
        raise Rejected(str(exc)) from exc
    advertised = link_rel_manifest(page, locator)
    if advertised:
        return _fetch_manifest(advertised, fetch), advertised, SOURCE_LINK_REL

    well_known = urllib.parse.urlunsplit(("https", parts.netloc, WELL_KNOWN_PATH, "", ""))
    return _fetch_manifest(well_known, fetch), well_known, SOURCE_WELL_KNOWN


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def verify_signature(document: dict) -> str:
    """The public key the manifest is validly signed with, or Rejected."""
    if not document.get("signature"):
        raise Rejected("the manifest is unsigned; the catalog lists signed releases only (SPEC 8)")
    try:
        verified = signing.verify_manifest(document)
    except signing.SigningError as exc:
        raise Rejected(f"signature check failed: {exc}") from exc
    if not verified:
        raise Rejected("the signature does not verify against signing.public_key")
    return str(document["signing"]["public_key"])


def origin_is_publisher(manifest_url: str, publisher: str) -> bool:
    host = (urllib.parse.urlsplit(manifest_url).hostname or "").lower()
    return host == publisher or host.endswith("." + publisher)


def bind_publisher(manifest_url: str, publisher: str, name: str, public_key: str,
                   key_id: str | None, fetch: repository.Fetch) -> str:
    """Which channel ties ``public_key`` to ``publisher`` (a ``BINDING_*``
    value), or Rejected when none does."""
    if origin_is_publisher(manifest_url, publisher):
        return BINDING_ORIGIN
    directory_url = signing.key_directory_url(publisher)
    try:
        body = fetch(directory_url, MAX_KEY_DIRECTORY_BYTES)
    except repository.Absent as exc:
        raise Rejected(
            f"nothing binds the signing key to {publisher}: the manifest was not served from "
            f"{publisher} and {directory_url} does not exist (SPEC 8.5)"
        ) from exc
    except repository.RepositoryError as exc:
        raise Rejected(f"could not read the key directory of {publisher}: {exc}") from exc
    try:
        keys = signing.parse_key_directory(json.loads(body.decode("utf-8")), publisher=publisher, name=name)
    except (UnicodeDecodeError, ValueError, signing.SigningError) as exc:
        raise Rejected(f"the key directory of {publisher} is not usable: {exc}") from exc
    if public_key not in keys:
        shown = f" (key_id {key_id})" if key_id else ""
        raise Rejected(
            f"the key directory of {publisher} does not list the key this release is signed "
            f"with{shown} for {name}"
        )
    return BINDING_KEY_DIRECTORY


def admit(raw_locator: str, fetch: repository.Fetch) -> Package:
    """Run the whole gate on a submission; the Package to list, or Rejected."""
    locator = normalize_locator(raw_locator)
    document, manifest_url, source = discover(locator, fetch)
    try:
        summary = axp_manifest.validate(document)
    except axp_manifest.ManifestError as exc:
        raise Rejected(f"not a valid AXP manifest: {exc}") from exc
    if summary["publisher_derived"]:
        raise Rejected("identity.publisher is required; a v0.2 manifest without one is not listed")
    public_key = verify_signature(document)
    signing_block = document.get("signing") or {}
    key_id = signing_block.get("key_id") or None
    binding = bind_publisher(manifest_url, summary["publisher"], summary["name"], public_key, key_id, fetch)
    if updates.is_expired(document, grace_days=0):
        raise Rejected("the release is past its valid_until date; publish a fresh release first")

    identity = document["identity"]
    release = document.get("release") or {}
    return Package(
        ext_id=summary["ext_id"],
        publisher=summary["publisher"],
        name=summary["name"],
        version=summary["version"],
        display_name=str(identity["display_name"]),
        description=str(identity["description"]),
        homepage=identity.get("homepage") or None,
        license=identity.get("license") or None,
        keywords=tuple(str(k) for k in identity.get("keywords") or ()),
        channel=summary["channel"],
        runtimes=tuple(summary["runtimes"]),
        provides=tuple(summary["provides"]),
        permissions=dict(document.get("permissions") or {}),
        public_key=public_key,
        key_id=key_id,
        locator=locator,
        manifest_url=manifest_url,
        source=source,
        binding=binding,
        published_at=release.get("published_at") or None,
        valid_until=release.get("valid_until") or None,
        manifest=document,
    )
