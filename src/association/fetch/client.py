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

#: Page size asked for when reading a paged core-API collection.
#:
#: ESPN's default is 25. Every collection this project reads is far smaller
#: than this (the power index tops out at 90 rows a season), so one request
#: normally suffices and the paging loop in
#: :meth:`ESPNClient.get_collection` is the belt to that braces.
#:
#: .. versionadded:: 2.2.0
COLLECTION_PAGE_SIZE = 1000


def _warn_if_truncated(url: str, data: Any) -> None:
    """Log when `data` is one page of a collection with more pages behind it."""
    if not isinstance(data, dict):
        return
    pages = data.get("pageCount")
    if isinstance(pages, int) and pages > 1 and isinstance(data.get("items"), list):
        log.warning(
            "%s returned page %s of %s (%s of %s items) - this is a collection, read it with get_collection()",
            url,
            data.get("pageIndex"),
            pages,
            len(data["items"]),
            data.get("count"),
        )


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
        resource).

        Warns when the response is one page of a longer collection. ESPN's core
        API answers a collection with ``{count, pageIndex, pageSize, pageCount,
        items}`` and a default ``pageSize`` of 25, and a short page looks exactly
        like a short dataset: reading page 1 of the power index stored 25 of
        ESPN's 90 rows a season for months, with no error and a valid shape, and
        ``DATA.md`` recorded the missing 65 as ESPN "keeping only postseason
        teams". Anything reading a collection wants :meth:`get_collection`.

        .. versionchanged:: 2.2.0
           Logs a warning when it returns an unexhausted page of a collection.
        """
        data = self._request_json(url, params)
        _warn_if_truncated(url, data)
        return data

    def get_collection(self, url: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Every item of a paged core-API collection, not just the first page.

        ESPN's ``sports.core.api.espn.com`` endpoints that return a *collection*
        (rather than one athlete's or one team's statistics) page at 25 by
        default. This asks for a large page and then keeps requesting pages
        until it holds the ``count`` the response itself declares, so a page
        size that changes under us cannot silently shorten the answer.

        Returns an empty list where :meth:`get_json` would return None, since a
        missing collection and an empty one are the same thing to every caller
        here.

        Logs at WARNING when the *first* page cannot be read at all - a 400 or
        404 (``_request_json`` returns ``None`` for both), or a 200 whose body
        is not the paged-collection shape. That is the case the declared-vs-fetched
        check below cannot see: with no first page there is no ``count`` to
        compare against, so a caller reading the returned ``[]`` would otherwise
        get no signal that anything went wrong - indistinguishable from a
        collection that is genuinely empty. A later page failing the same way is
        already loud: page one's ``count`` is on record by then, and the
        declared-vs-fetched warning below fires because ``len(items)`` falls
        short of it.

        .. versionadded:: 2.2.0
        .. versionchanged:: 4.0.1
           Warns when the first page itself cannot be read, instead of
           returning ``[]`` with nothing logged.
        """
        items: list[Any] = []
        page = 1
        expected: int | None = None
        while True:
            merged = {**(params or {}), "limit": COLLECTION_PAGE_SIZE, "page": page}
            data = self._request_json(url, merged)
            if not isinstance(data, dict):
                if page == 1:
                    log.warning("collection %s: first page was not a JSON object (got %s) - returning no items", url, type(data).__name__)
                break
            batch = data.get("items")
            if not isinstance(batch, list):
                if page == 1:
                    log.warning("collection %s: first page had no items list (got %s) - returning no items", url, type(batch).__name__)
                break
            items.extend(batch)
            if expected is None and isinstance(data.get("count"), int):
                expected = data["count"]
            pages = data.get("pageCount")
            if not batch or not isinstance(pages, int) or page >= pages:
                break
            page += 1
        # A mismatch is a finding, not a crash: the rows fetched are still real,
        # and a loud log beside a short write is what this method exists to
        # produce. Raising would abort a whole pull over one endpoint.
        if expected is not None and len(items) != expected:
            log.warning("collection %s declared %d items, fetched %d", url, expected, len(items))
        return items

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """One GET with throttling and retries, and no collection check."""
        last_exc = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 - connection errors and timeouts, whose types vary by transport; retried
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
