"""The catalog store: one SQLite row per extension id.

A re-submission (or a refresh) of an already listed extension replaces the
row with the release the gate admitted this time - the catalog mirrors what
the publisher currently publishes, it keeps no release history. ``listed_at``
survives replacement; ``refreshed_at`` moves every time.

Every call opens its own connection: the service runs a single worker, the
write rate is a few rows per day, and a connection per call keeps the store
free of thread-affinity rules.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .gate import Package

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    ext_id        TEXT PRIMARY KEY,
    publisher     TEXT NOT NULL,
    name          TEXT NOT NULL,
    version       TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    description   TEXT NOT NULL,
    homepage      TEXT,
    license       TEXT,
    keywords      TEXT NOT NULL,   -- JSON array
    channel       TEXT NOT NULL,
    runtimes      TEXT NOT NULL,   -- JSON array
    provides      TEXT NOT NULL,   -- JSON array of component types
    permissions   TEXT NOT NULL,   -- JSON object
    public_key    TEXT NOT NULL,
    key_id        TEXT,
    locator       TEXT NOT NULL,
    manifest_url  TEXT NOT NULL,
    source        TEXT NOT NULL,
    binding       TEXT NOT NULL,
    published_at  TEXT,
    valid_until   TEXT,
    manifest      TEXT NOT NULL,   -- the admitted document, JSON
    listed_at     TEXT NOT NULL,
    refreshed_at  TEXT NOT NULL,
    refresh_error TEXT
);
CREATE INDEX IF NOT EXISTS packages_publisher ON packages (publisher);
"""

_JSON_COLUMNS = ("keywords", "runtimes", "provides", "permissions", "manifest")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Listing:
    """A package plus its catalog bookkeeping, as read back from the store."""

    def __init__(self, package: Package, listed_at: str, refreshed_at: str, refresh_error: str | None) -> None:
        self.package = package
        self.listed_at = listed_at
        self.refreshed_at = refreshed_at
        self.refresh_error = refresh_error

    def as_dict(self) -> dict:
        return {
            **self.package.as_dict(),
            "listed_at": self.listed_at,
            "refreshed_at": self.refreshed_at,
            "refresh_error": self.refresh_error,
        }


class Store:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # -- writes -------------------------------------------------------------

    def upsert(self, package: Package) -> Listing:
        """List ``package``, replacing any earlier release of the same extension."""
        now = _now()
        row = {
            **package.as_dict(),
            "listed_at": now,
            "refreshed_at": now,
            "refresh_error": None,
        }
        for column in _JSON_COLUMNS:
            row[column] = json.dumps(row[column], sort_keys=True)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{column}" for column in row)
        updates = ", ".join(f"{column} = excluded.{column}" for column in row if column != "listed_at")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO packages ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(ext_id) DO UPDATE SET {updates}",
                row,
            )
        listing = self.get(package.ext_id)
        assert listing is not None
        return listing

    def mark_refresh_error(self, ext_id: str, error: str) -> None:
        """Record that the last refresh of ``ext_id`` failed; the listing stays."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE packages SET refreshed_at = ?, refresh_error = ? WHERE ext_id = ?",
                (_now(), error[:2000], ext_id),
            )

    def delete(self, ext_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM packages WHERE ext_id = ?", (ext_id,))
            return cursor.rowcount > 0

    # -- reads --------------------------------------------------------------

    def get(self, ext_id: str) -> Listing | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM packages WHERE ext_id = ?", (ext_id,)).fetchone()
        return _listing(row) if row else None

    def search(self, query: str = "", *, publisher: str | None = None) -> list[Listing]:
        """Listings matching every word of ``query`` (name, display name,
        description, publisher or a keyword), newest listing first."""
        sql = "SELECT * FROM packages"
        clauses: list[str] = []
        parameters: list[str] = []
        if publisher:
            clauses.append("publisher = ?")
            parameters.append(publisher)
        for word in query.split():
            clauses.append(
                "(instr(lower(ext_id), ?) OR instr(lower(display_name), ?) "
                "OR instr(lower(description), ?) OR instr(lower(keywords), ?))"
            )
            parameters.extend([word.lower()] * 4)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY listed_at DESC, ext_id"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [_listing(row) for row in rows]

    def all(self) -> list[Listing]:
        return self.search()

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT count(*) FROM packages").fetchone()[0])

    def publishers(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT publisher, count(*) AS n FROM packages GROUP BY publisher ORDER BY n DESC, publisher"
            ).fetchall()
        return [(row["publisher"], int(row["n"])) for row in rows]


def _listing(row: sqlite3.Row) -> Listing:
    data = dict(row)
    for column in _JSON_COLUMNS:
        data[column] = json.loads(data[column])
    listed_at = data.pop("listed_at")
    refreshed_at = data.pop("refreshed_at")
    refresh_error = data.pop("refresh_error")
    data["keywords"] = tuple(data["keywords"])
    data["runtimes"] = tuple(data["runtimes"])
    data["provides"] = tuple(data["provides"])
    return Listing(Package(**data), listed_at, refreshed_at, refresh_error)
