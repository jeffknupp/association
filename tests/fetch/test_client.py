"""Regression + sanity tests for the ESPN HTTP client."""

import itertools
from typing import Any

import pytest
from curl_cffi import requests as cf_requests

from association.fetch.client import ESPNClient


def test_client_uses_curl_cffi_not_plain_requests() -> None:
    """Regression: ESPN's CDN does TLS-fingerprint bot mitigation - plain
    `requests`/`httpx` get a 403 even with a real browser User-Agent header;
    only curl (and curl_cffi's browser TLS impersonation) get through. Guards
    against someone "simplifying" this back to plain `requests` and silently
    breaking every fetch."""
    client = ESPNClient()
    assert isinstance(client.session, cf_requests.Session)


def test_get_json_returns_none_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ESPNClient()

    class FakeResp:
        status_code = 404
        content = b""

    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResp())
    assert client.get_json("http://example.com") is None


def test_get_json_returns_none_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ESPNClient()

    class FakeResp:
        status_code = 400
        content = b""

    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResp())
    assert client.get_json("http://example.com") is None


def test_get_json_parses_body_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ESPNClient()

    class FakeResp:
        status_code = 200
        content = b'{"a": 1}'

        def json(self) -> dict:
            return {"a": 1}

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResp())
    assert client.get_json("http://example.com") == {"a": 1}


def test_get_json_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ESPNClient(max_retries=3)
    monkeypatch.setattr(client, "_sleep_backoff", lambda attempt: None)  # skip real sleep
    calls = {"n": 0}

    class ErrResp:
        status_code = 503
        content = b""

    class OkResp:
        status_code = 200
        content = b'{"ok": true}'

        def json(self) -> dict:
            return {"ok": True}

        def raise_for_status(self) -> None:
            pass

    def fake_get(*a: object, **k: object) -> ErrResp | OkResp:
        calls["n"] += 1
        return ErrResp() if calls["n"] < 3 else OkResp()

    monkeypatch.setattr(client.session, "get", fake_get)
    result = client.get_json("http://example.com")
    assert result == {"ok": True}
    assert calls["n"] == 3


def test_get_json_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ESPNClient(max_retries=2)
    monkeypatch.setattr(client, "_sleep_backoff", lambda attempt: None)

    class ErrResp:
        status_code = 503
        content = b""

    monkeypatch.setattr(client.session, "get", lambda *a, **k: ErrResp())
    with pytest.raises(RuntimeError):
        client.get_json("http://example.com")


# ---------------- concurrency ----------------


def test_each_thread_gets_its_own_session() -> None:
    """A curl_cffi session wraps one libcurl handle and cannot be shared, so
    workers must not fetch through the same one."""
    import threading

    client = ESPNClient()
    main = client.session
    # The sessions themselves are kept, not their ids: a thread-local session is
    # freed when its thread ends, and the next one can land on the same address.
    seen: list[object] = []
    lock = threading.Lock()

    def record() -> None:
        session = client.session
        with lock:
            seen.append(session)

    threads = [threading.Thread(target=record) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(session) for session in seen}) == 3
    assert all(session is not main for session in seen)
    assert client.session is main  # and the caller keeps the one it had


def test_the_rate_limit_is_shared_between_threads() -> None:
    """The rate limit is a promise about how hard this hits ESPN. Per-thread
    allowances would multiply it by the worker count - eight workers at
    --rate-limit 10 would mean 80 requests/second."""
    import threading
    import time

    client = ESPNClient(rate_limit=50)  # 20ms apart
    stamps: list[float] = []
    lock = threading.Lock()

    def issue() -> None:
        client._throttle()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=issue) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stamps.sort()
    gaps = [b - a for a, b in itertools.pairwise(stamps)]
    # Generous slack: a loaded machine makes gaps LARGER, never smaller, so a
    # floor is the safe thing to assert.
    assert min(gaps) >= 0.015, gaps
    assert len(stamps) == 6


def test_no_throttling_at_all_when_the_rate_limit_is_zero() -> None:
    client = ESPNClient(rate_limit=0)
    client._throttle()  # must not raise, must not block
    assert client._min_interval == 0.0


# ---------------- paged collections ----------------


class _PagedSession:
    """A session that answers like ESPN's core API: `count` items in pages of
    `page_size`, wrapped in the envelope that endpoint really returns."""

    def __init__(self, count: int, page_size: int = 25) -> None:
        self.count, self.page_size = count, page_size
        self.requests: list[tuple[int, int]] = []

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> Any:
        params = params or {}
        size = int(params.get("limit") or self.page_size)
        page = int(params.get("page") or 1)
        self.requests.append((page, size))
        start = (page - 1) * size
        items = [{"i": n} for n in range(start, min(start + size, self.count))]
        body = {
            "count": self.count,
            "pageIndex": page,
            "pageSize": size,
            "pageCount": max(1, -(-self.count // size)),
            "items": items,
        }

        class Resp:
            status_code = 200
            content = b"{}"

            def json(self) -> dict:
                return body

            def raise_for_status(self) -> None:
                pass

        return Resp()


def test_get_collection_returns_every_item_not_the_first_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug this exists for. ESPN's power index answers `count: 90` in pages
    of 25, and reading page 1 stored 25 rows a season for months - no error, a
    valid shape, and `DATA.md` recorded the missing 65 as ESPN "keeping only
    postseason teams"."""
    client = ESPNClient()
    session = _PagedSession(count=90)
    monkeypatch.setattr(ESPNClient, "session", property(lambda self: session))
    items = client.get_collection("http://example.com/powerindex")
    assert len(items) == 90
    assert items[0] == {"i": 0} and items[-1] == {"i": 89}


def test_get_collection_pages_when_the_server_caps_the_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for a big page is the fast path, not the guarantee: a server that
    caps `limit` must still be read to the end, or this method would truncate
    exactly like the call it replaces."""
    client = ESPNClient()
    session = _PagedSession(count=90, page_size=25)

    def capped(url: str, params: dict | None = None, timeout: float | None = None) -> Any:
        params = dict(params or {})
        params["limit"] = 25  # the server ignores what we asked for
        return _PagedSession.get(session, url, params, timeout)

    monkeypatch.setattr(ESPNClient, "session", property(lambda self: session))
    monkeypatch.setattr(session, "get", capped)
    items = client.get_collection("http://example.com/powerindex")
    assert len(items) == 90
    assert [page for page, _ in session.requests] == [1, 2, 3, 4]


def test_get_collection_warns_when_it_holds_fewer_than_the_declared_count(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A short read is a finding, not a crash: the rows fetched are real, and
    aborting a whole pull over one endpoint would be worse than saying so."""
    client = ESPNClient()

    class Lying(_PagedSession):
        def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> Any:
            resp = super().get(url, params, timeout)
            body = resp.json()
            body["count"] = 90  # claims 90, serves one page and says so
            body["pageCount"] = 1
            return resp

    session = Lying(count=25)
    monkeypatch.setattr(ESPNClient, "session", property(lambda self: session))
    with caplog.at_level("WARNING"):
        items = client.get_collection("http://example.com/powerindex")
    assert len(items) == 25
    assert "declared 90 items, fetched 25" in caplog.text


def test_get_json_warns_when_it_returns_one_page_of_a_collection(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """The guard that makes this class of bug loud for any endpoint added later.
    A short page and a short dataset are indistinguishable without it."""
    client = ESPNClient()
    session = _PagedSession(count=90)
    monkeypatch.setattr(ESPNClient, "session", property(lambda self: session))
    with caplog.at_level("WARNING"):
        data = client.get_json("http://example.com/powerindex")
    assert isinstance(data, dict)
    assert len(data["items"]) == 25
    assert "page 1 of 4" in caplog.text and "get_collection" in caplog.text


def test_get_json_stays_quiet_for_a_single_page_response(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Every other endpoint this project reads is a single resource or a nested
    document, and must not start logging warnings."""
    client = ESPNClient()
    session = _PagedSession(count=10)
    monkeypatch.setattr(ESPNClient, "session", property(lambda self: session))
    with caplog.at_level("WARNING"):
        client.get_json("http://example.com/onepage")
    assert caplog.text == ""
