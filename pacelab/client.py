"""Small OpenF1 client with a SQLite response cache and client-side rate limiting.

Free tier: 3 requests/second and 30 requests/minute, historical data only.
Sponsor tier: 6 requests/second and 60 requests/minute, plus live data during
sessions. Live access needs an access token; see `OpenF1Client.from_env`.

Data from finished sessions never changes, so it is cached forever. Data from a
running session gets a short TTL, and live polling skips the cache entirely.
"""

from __future__ import annotations

import collections
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests

BASE_URL = "https://api.openf1.org/v1"
TOKEN_URL = "https://api.openf1.org/token"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "openf1.sqlite"

MAX_RETRIES = 5
FILTER_OPERATORS = ("<=", ">=", "<", ">")
# Empty answers may just mean "not published yet", so re-check them after a minute.
EMPTY_RESULT_TTL_S = 60

# Published limits, with a little headroom.
FREE_LIMITS = (2.5, 28)
SPONSOR_LIMITS = (5.5, 56)


class OpenF1Error(RuntimeError):
    pass


class OpenF1LockedError(OpenF1Error):
    """Free access is closed: OpenF1 restricts the whole API to sponsors while a
    session is live, including historical data."""


class RateLimiter:
    """Blocks until a request fits both a per-second and a per-minute budget."""

    def __init__(self, per_second: float, per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = 1.0 / per_second
        self.per_minute = per_minute
        self.calls: collections.deque[float] = collections.deque()
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()

    def wait(self) -> float:
        """Reserve a slot; returns how long it waited."""
        waited = 0.0
        with self._lock:
            while True:
                now = self.clock()
                while self.calls and now - self.calls[0] >= 60:
                    self.calls.popleft()
                delays = []
                if len(self.calls) >= self.per_minute:
                    delays.append(60 - (now - self.calls[0]))
                if self.calls and now - self.calls[-1] < self.min_interval:
                    delays.append(self.min_interval - (now - self.calls[-1]))
                if not delays:
                    self.calls.append(now)
                    return waited
                delay = max(delays)
                self.sleep(delay)
                waited += delay


class OpenF1Client:
    def __init__(
        self,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        access_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", "f1-pace-lab/0.2")
        self._username = username
        self._password = password
        self._token = access_token
        if self._token is None and username and password:
            self._token = self._request_token()
        if self._token:
            self.http.headers["Authorization"] = f"Bearer {self._token}"
        self.limiter = RateLimiter(*(SPONSOR_LIMITS if self.authenticated else FREE_LIMITS))

        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                " url TEXT PRIMARY KEY, fetched_at REAL NOT NULL, body TEXT NOT NULL)"
            )

    @classmethod
    def from_env(cls, **kwargs: Any) -> "OpenF1Client":
        """Create a client, authenticated if OpenF1 credentials are set in the environment.

        Set either OPENF1_ACCESS_TOKEN, or OPENF1_USERNAME and OPENF1_PASSWORD
        (the account you get when you sponsor OpenF1). Without them the client
        uses the free tier.
        """
        return cls(
            access_token=os.environ.get("OPENF1_ACCESS_TOKEN") or None,
            username=os.environ.get("OPENF1_USERNAME") or None,
            password=os.environ.get("OPENF1_PASSWORD") or None,
            **kwargs,
        )

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _request_token(self) -> str:
        resp = self.http.post(
            TOKEN_URL,
            data={"username": self._username, "password": self._password},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise OpenF1Error(f"OpenF1 login failed ({resp.status_code}). Check OPENF1_USERNAME/OPENF1_PASSWORD.")
        token = resp.json().get("access_token")
        if not token:
            raise OpenF1Error("OpenF1 login returned no access_token.")
        return token

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.cache_path)

    def _url(self, endpoint: str, params: dict[str, Any] | None) -> str:
        # OpenF1 filters put the operator between key and value with no '=',
        # e.g. "date>2026-11-29T16:00:00" or "lap_duration<=90".
        parts = []
        for key, value in sorted((params or {}).items()):
            if key.endswith(FILTER_OPERATORS):
                parts.append(quote(key, safe="") + quote(str(value), safe=":"))
            else:
                parts.append(urlencode({key: value}))
        query = "&".join(parts)
        return f"{self.base_url}/{endpoint.strip('/')}" + (f"?{query}" if query else "")

    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        ttl: float | None = None,
        cache: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch an endpoint as a list of records.

        Filter operators go in the key, e.g. {"date>": "2026-11-29T16:00:00"}.
        ttl=None caches forever; ttl=0 always refetches; cache=False neither reads
        nor writes the cache (for live polling). An empty result is never cached
        forever, because OpenF1 publishes session data shortly *after* a session.
        """
        url = self._url(endpoint, params)
        cached = self._read_cache(url) if cache else None
        if cached is not None:
            fetched_at, body = cached
            age = time.time() - fetched_at
            fresh = (bool(body) or age < EMPTY_RESULT_TTL_S) if ttl is None else age < ttl
            if fresh:
                return body

        try:
            body = self._fetch(url)
        except OpenF1LockedError:
            # During a live session the free API is closed; a stale copy beats nothing.
            if cached is not None:
                return cached[1]
            raise
        if cache:
            self._write_cache(url, body)
        return body

    def _fetch(self, url: str) -> list[dict[str, Any]]:
        backoff = 2.0
        reauthed = False
        for _ in range(MAX_RETRIES):
            self.limiter.wait()
            resp = self.http.get(url, timeout=30)
            if resp.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 401 and self._username and not reauthed:
                # Tokens expire; log in again once.
                self._token = self._request_token()
                self.http.headers["Authorization"] = f"Bearer {self._token}"
                reauthed = True
                continue
            if resp.status_code == 401:
                raise OpenF1LockedError(
                    "OpenF1 is closed to free users while a session is live (historical data "
                    "included). It reopens when the session ends."
                    if "session in progress" in resp.text.lower()
                    else "OpenF1 refused the request (401). Live data needs a sponsor account."
                )
            if resp.status_code == 404:
                # OpenF1 answers "No results found." with a 404.
                return []
            if resp.status_code >= 400:
                raise OpenF1Error(f"{resp.status_code} from {url}: {resp.text[:200]}")
            data = resp.json()
            if isinstance(data, dict):
                if "No results" in str(data.get("detail", "")):
                    return []
                raise OpenF1Error(f"Unexpected response from {url}: {data}")
            return data
        raise OpenF1Error(f"Rate limited too many times: {url}")

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
