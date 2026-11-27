"""Small OpenF1 client with a SQLite response cache and a client-side rate limit.

OpenF1 allows at most 3 requests per second on the free tier, so every request goes
through a throttle, 429 responses are retried with backoff, and responses are cached
locally. Data from finished sessions never changes, so it can be cached forever;
data from a session that is running (or only just ended) gets a short TTL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

BASE_URL = "https://api.openf1.org/v1"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "openf1.sqlite"

# Free tier limit is 3 req/s; stay a little under it.
MIN_INTERVAL_S = 0.4
MAX_RETRIES = 5
# Empty answers may just mean "not published yet", so re-check them after a minute.
EMPTY_RESULT_TTL_S = 60


class OpenF1Error(RuntimeError):
    pass


class OpenF1Client:
    def __init__(
        self,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", "f1-pace-lab/0.1")
        self._lock = threading.Lock()
        self._last_request = 0.0

        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                " url TEXT PRIMARY KEY, fetched_at REAL NOT NULL, body TEXT NOT NULL)"
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.cache_path)

    def _url(self, endpoint: str, params: dict[str, Any] | None) -> str:
        query = urlencode(sorted((params or {}).items()))
        return f"{self.base_url}/{endpoint.strip('/')}" + (f"?{query}" if query else "")

    def get(
        self, endpoint: str, params: dict[str, Any] | None = None, ttl: float | None = None
    ) -> list[dict[str, Any]]:
        """Fetch an endpoint as a list of records.

        ttl=None caches forever; ttl=0 always refetches. An empty result is never
        cached forever, because OpenF1 publishes session data shortly *after* a session.
        """
        url = self._url(endpoint, params)
        cached = self._read_cache(url)
        if cached is not None:
            fetched_at, body = cached
            age = time.time() - fetched_at
            if ttl is None:
                fresh = bool(body) or age < EMPTY_RESULT_TTL_S
            else:
                fresh = age < ttl
            if fresh:
                return body

        body = self._fetch(url)
        self._write_cache(url, body)
        return body

    def _fetch(self, url: str) -> list[dict[str, Any]]:
        backoff = 1.0
        for _ in range(MAX_RETRIES):
            self._throttle()
            resp = self.http.get(url, timeout=30)
            if resp.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 404:
                # OpenF1 answers "No results found." with a 404.
                return []
            if resp.status_code >= 400:
                raise OpenF1Error(f"{resp.status_code} from {url}: {resp.text[:200]}")
            data = resp.json()
            if isinstance(data, dict):
                # Some errors come back as {"detail": ...} with a 200.
                if "No results" in str(data.get("detail", "")):
                    return []
                raise OpenF1Error(f"Unexpected response from {url}: {data}")
            return data
        raise OpenF1Error(f"Rate limited too many times: {url}")

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _read_cache(self, url: str) -> tuple[float, list[dict[str, Any]]] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT fetched_at, body FROM responses WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        return row[0], json.loads(row[1])

    def _write_cache(self, url: str, body: list[dict[str, Any]]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO responses (url, fetched_at, body) VALUES (?, ?, ?)",
                (url, time.time(), json.dumps(body)),
            )
