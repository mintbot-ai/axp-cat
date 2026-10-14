"""The axp.cat web app: the AXP introduction, the catalog and the submission form.

Built with :func:`create_app` so tests and the service can hand in their own
settings, store and fetcher; importing the module has no side effects. The
service runs ``uvicorn --factory axpcat.app:create_app``.

Every route is a plain ``def``: Starlette runs them on its thread pool, which
is what the blocking outbound fetches of a submission need.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, config, gate
from . import fetch as safe_fetch
from .store import Listing, Store

logger = logging.getLogger("axpcat")

_HERE = Path(__file__).resolve().parent

# Server-rendered HTML with its own stylesheet; nothing is loaded from
# anywhere else, forms post back to the site only.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'none'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}

Fetch = Callable[[str, int], bytes]


class SubmitLimiter:
    """At most ``per_hour`` submissions per client key, sliding window."""

    def __init__(self, per_hour: int) -> None:
        self._per_hour = per_hour
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._seen.setdefault(key, deque())
            while window and now - window[0] > 3600:
                window.popleft()
            if len(window) >= self._per_hour:
                return False
            window.append(now)
            return True


def client_key(request: Request) -> str:
    """The visitor's address: nginx's X-Real-IP in production, the socket peer otherwise."""
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded.strip()
    return request.client.host if request.client else "unknown"


def create_app(settings: config.Settings | None = None, store: Store | None = None,
               fetch: Fetch | None = None) -> FastAPI:
    settings = settings or config.load()
    store = store or Store(settings.db_path)
    if fetch is None:
        timeout = settings.fetch_timeout_s

        def fetch(url: str, max_bytes: int) -> bytes:
            return safe_fetch.fetch(url, max_bytes, timeout=timeout)

    limiter = SubmitLimiter(settings.submit_per_hour)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    templates.env.globals.update(
        site={
            "base": settings.public_base,
            "version": __version__,
            "spec_url": config.SPEC_URL,
            "spec_repo_url": config.SPEC_REPO_URL,
            "site_repo_url": config.SITE_REPO_URL,
        },
    )
    templates.env.filters["pretty_json"] = lambda value: json.dumps(value, indent=2, sort_keys=True)

    app = FastAPI(title="axp.cat", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    # -- pages -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render(request, "index.html", count=store.count(), latest=store.all()[:6])

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog(request: Request, q: str = "", publisher: str = ""):
        q = q.strip()[:200]
        publisher = publisher.strip().lower()[:253]
        return render(
            request, "catalog.html",
            listings=store.search(q, publisher=publisher or None),
            publishers=store.publishers(), q=q, publisher=publisher, count=store.count(),
        )

    @app.get("/catalog/{publisher}/{name}", response_class=HTMLResponse)
    def package(request: Request, publisher: str, name: str):
        listing = store.get(f"{publisher}/{name}")
        if listing is None:
            return render(request, "404.html", status_code=404, ext_id=f"{publisher}/{name}")
        return render(request, "package.html", listing=listing, p=listing.package)

    @app.get("/submit", response_class=HTMLResponse)
    def submit_form(request: Request):
        return render(request, "submit.html", locator="", error=None)

    @app.post("/submit", response_class=HTMLResponse)
    def submit(request: Request, locator: str = Form("")):
        if not limiter.allow(client_key(request)):
            return render(request, "submit.html", status_code=429, locator=locator,
                          error=f"too many submissions from your address; try again in an hour")
        try:
            package = gate.admit(locator, fetch)
        except gate.Rejected as exc:
            logger.info("rejected %r: %s", locator, exc)
            return render(request, "submit.html", status_code=422, locator=locator, error=str(exc))
        listing = store.upsert(package)
        logger.info("listed %s %s from %s", package.ext_id, package.version, package.manifest_url)
        return RedirectResponse(f"/catalog/{listing.package.ext_id}", status_code=303)

    # -- machine-readable ----------------------------------------------------

    @app.get("/api/catalog.json")
    def api_catalog(q: str = "", publisher: str = ""):
        listings = store.search(q.strip()[:200], publisher=publisher.strip().lower() or None)
        return JSONResponse({"count": len(listings), "packages": [_summary(listing) for listing in listings]})

    @app.get("/api/catalog/{publisher}/{name}.json")
    def api_package(publisher: str, name: str):
        listing = store.get(f"{publisher}/{name}")
        if listing is None:
            return JSONResponse({"error": "not listed"}, status_code=404)
        return JSONResponse(listing.as_dict())

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "packages": store.count(), "version": __version__}

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /submit\n")

    return app


def _summary(listing: Listing) -> dict:
    p = listing.package
    return {
        "ext_id": p.ext_id,
        "publisher": p.publisher,
        "name": p.name,
        "version": p.version,
        "display_name": p.display_name,
        "description": p.description,
        "channel": p.channel,
        "runtimes": list(p.runtimes),
        "provides": list(p.provides),
        "locator": p.locator,
        "manifest_url": p.manifest_url,
        "listed_at": listing.listed_at,
    }
