"""Thin HTTP client for ESPN's undocumented stats APIs: retry/backoff, rate limiting.

ESPN's CDN does TLS-fingerprint-based bot mitigation - plain `requests`/`httpx`
get a 403 even with a browser User-Agent header, while curl (and browser TLS
handshakes) succeed. curl_cffi impersonates a real browser's TLS fingerprint,
confirmed live against site.api.espn.com, so it's used here instead of `requests`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from curl_cffi import requests as cf_requests

log = logging.getLogger("association.fetch.client")

IMPERSONATE = "chrome124"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
NOT_FOUND_STATUS = {400, 404}


class ESPNClient:
    def __init__(self, rate_limit: float = 5.0, timeout: float = 15.0, max_retries: int = 5):
        """rate_limit: max requests/second against ESPN's hosts."""
        self.session: cf_requests.Session = cf_requests.Session(impersonate=IMPERSONATE)
        self.timeout = timeout
        self.max_retries = max_retries
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._last_request = 0.0

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
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
