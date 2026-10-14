"""Runtime settings, read once from the environment.

Everything a deployment varies lives here so the service unit is the same
file on every host: the public base URL (canonical links), the SQLite path,
the fetch timeout and the submission rate limit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SPEC_REPO_URL = "https://github.com/mintbot-ai/agent-extension"
SPEC_URL = f"{SPEC_REPO_URL}/blob/main/SPEC.md"
SITE_REPO_URL = "https://github.com/mintbot-ai/axp-cat"


@dataclass(frozen=True)
class Settings:
    public_base: str = "https://axp.cat"
    db_path: Path = Path("/var/lib/axpcat/catalog.db")
    fetch_timeout_s: float = 15.0
    # Submissions one client address may make per hour. Each submission
    # costs a handful of outbound fetches, so this is a courtesy cap, not
    # a security boundary.
    submit_per_hour: int = 12


def load() -> Settings:
    defaults = Settings()
    return Settings(
        public_base=os.environ.get("AXPCAT_PUBLIC_BASE", defaults.public_base).rstrip("/"),
        db_path=Path(os.environ.get("AXPCAT_DB", str(defaults.db_path))),
        fetch_timeout_s=float(os.environ.get("AXPCAT_FETCH_TIMEOUT", defaults.fetch_timeout_s)),
        submit_per_hour=int(os.environ.get("AXPCAT_SUBMIT_PER_HOUR", defaults.submit_per_hour)),
    )
