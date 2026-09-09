"""Thin HTTP client for ESPN's undocumented stats APIs: retry/backoff, rate limiting.

ESPN's CDN does TLS-fingerprint-based bot mitigation - plain `requests`/`httpx`
get a 403 even with a browser User-Agent header, while curl (and browser TLS
handshakes) succeed. curl_cffi impersonates a real browser's TLS fingerprint,
confirmed live against site.api.espn.com, so it's used here instead of `requests`.

Safe to call from several threads: each gets its own session, and the rate
limit is shared between them, so `--workers` raises how many requests are in
flight without raising how many are issued per second.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from curl_cffi import requests as cf_requests

log: logging.Logger = logging.getLogger("association.fetch.client")

IMPERSONATE = "chrome124"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
NOT_FOUND_STATUS = {400, 404}


class ESPNClient:
    """HTTP transport for ESPN's endpoints: throttling, retries, TLS impersonation.

    Uses ``curl_cffi`` rather than ``requests``/``httpx`` because ESPN's CDN
    fingerprints the TLS handshake and rejects the standard clients outright.
    Requests are rate limited (default 5/second) and retried with backoff.

    Thread-safe. Sessions are per-thread because a ``curl_cffi`` session wraps
    one libcurl handle and cannot be shared, while the throttle is deliberately
    shared: the rate limit is a promise about how hard this hits ESPN, and it
    would be worthless if each worker got its own allowance.

    .. versionchanged:: 1.6.0
       Usable from several threads at once. ``session`` is now per-thread; read
       it through :attr:`session` as before.
    """

    def __init__(self, rate_limit: float = 5.0, timeout: float = 15.0, max_retries: int = 5):
        """rate_limit: max requests/second against ESPN's hosts, across all threads."""
        self.timeout = timeout
        self.max_retries = max_retries
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._local = threading.local()

    @property
    def session(self) -> cf_requests.Session[cf_requests.Response]:
        """This thread's session, created on first use.

        The response type is spelled out rather than left to ``Session``'s
        default, because curl_cffi only *has* a default on Python 3.13 and up
        (``TypeVar(default=...)`` did not exist before it). Bare, the type is
        ``Session[Response]`` or ``Session[Unknown]`` depending on which branch of
        a ``sys.version_info`` check the type checker took - which is how this
        passed ``pyright --verifytypes`` on one interpreter and failed it on
        another.

        .. versionchanged:: 2.0.0
           Return type parameterized. The object returned is unchanged.
        """
        session = getattr(self._local, "session", None)
        if session is None:
            session = cf_requests.Session(impersonate=IMPERSONATE)
            self._local.session = session
        return session

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """GET url, return parsed JSON (a dict for every espn.com endpoint, but a
        bare list for e.g. NetPoints' player file), None on 400/404 (missing/invalid
        resource)."""
        last_exc = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as exc:  # connection errors, timeouts
                last_exc = exc
                log.warning("request error (attempt %d) %s: %s", attempt + 1, url, exc)
                self._sleep_backoff(attempt)
                continue

            if resp.status_code in NOT_FOUND_STATUS:
                return None
            if resp.status_code in RETRYABLE_STATUS:
                log.warning("status %d (attempt %d) %s", resp.status_code, attempt + 1, url)
                self._sleep_backoff(attempt)
                continue

            resp.raise_for_status()
            if not resp.content:
                return None
            return resp.json()

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Exceeded retries fetching {url}")

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(min(1.5 * (2**attempt), 30))

    def _throttle(self) -> None:
        """Space requests out by at least ``1 / rate_limit``, across every thread.

        The sleep happens while holding the lock. That serializes the *waiting*
        as well as the bookkeeping, which is the point: N workers must share one
        allowance, so at most one of them may be spacing itself at a time.
        Nothing else is done under the lock, and the wait is bounded by the
        interval, so it costs no concurrency on the requests themselves.
        """
        if self._min_interval <= 0:
            return
        with self._throttle_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
