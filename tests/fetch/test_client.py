"""Regression + sanity tests for the ESPN HTTP client."""

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
