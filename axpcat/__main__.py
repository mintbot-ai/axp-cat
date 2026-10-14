"""Command line: ``python -m axpcat check <locator>`` runs the gate without
listing anything (what a publisher runs before submitting);
``python -m axpcat refresh`` re-admits every listed package from its locator,
replacing the row with the current release or recording the failure."""

from __future__ import annotations

import argparse
import json
import sys

from . import config, gate
from . import fetch as safe_fetch
from .store import Store


def _fetcher(settings: config.Settings):
    def fetch(url: str, max_bytes: int) -> bytes:
        return safe_fetch.fetch(url, max_bytes, timeout=settings.fetch_timeout_s)

    return fetch


def cmd_check(args: argparse.Namespace) -> int:
    settings = config.load()
    try:
        package = gate.admit(args.locator, _fetcher(settings))
    except gate.Rejected as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    summary = package.as_dict()
    summary.pop("manifest")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    settings = config.load()
    store = Store(settings.db_path)
    fetch = _fetcher(settings)
    refreshed = failed = 0
    for listing in store.all():
        ext_id = listing.package.ext_id
        try:
            package = gate.admit(listing.package.locator, fetch)
        except gate.Rejected as exc:
            store.mark_refresh_error(ext_id, str(exc))
            failed += 1
            print(f"{ext_id}: refresh failed: {exc}", file=sys.stderr)
            continue
        if package.ext_id != ext_id:
            store.mark_refresh_error(ext_id, f"locator now publishes {package.ext_id}")
            failed += 1
            continue
        store.upsert(package)
        refreshed += 1
    print(f"refreshed {refreshed}, failed {failed}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="axpcat")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="run the listing gate on a locator without listing it")
    check.add_argument("locator")
    check.set_defaults(func=cmd_check)
    refresh = sub.add_parser("refresh", help="re-admit every listed package from its locator")
    refresh.set_defaults(func=cmd_refresh)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
