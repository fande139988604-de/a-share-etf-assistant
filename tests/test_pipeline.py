from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
from argparse import Namespace

import pytest

from etf_assistant.cli import collect, verify_url, write_json
from etf_assistant.core import INSTRUMENTS, build_snapshot, timestamp
from etf_assistant.provider import worker_call
from etf_assistant.worker import run_worker


def test_static_html_contains_exact_json_and_escapes_untrusted_text(tmp_path):
    from html.parser import HTMLParser
    from scripts.render_snapshot import render_snapshot

    document = build_snapshot({}, {}, timestamp("2026-10-09 16:30"))
    document["test_text"] = "<script>alert('test')</script>&中文"
    rendered = render_snapshot(document)
    assert "<script>" not in rendered

    class JSONBlock(HTMLParser):
        active = False
        fragments = []

        def handle_starttag(self, tag, attrs):
            if tag == "pre" and dict(attrs).get("id") == "snapshot-json":
                self.active = True

        def handle_endtag(self, tag):
            if tag == "pre":
                self.active = False

        def handle_data(self, text):
            if self.active:
                self.fragments.append(text)

    parser = JSONBlock()
    parser.feed(rendered)
    assert json.loads("".join(parser.fragments)) == document


def test_actual_akshare_parser_retains_same_response_identity(monkeypatch):
    import requests
    at = "2026-10-09 14:30"
    raw = {"data": {"code": "000510", "name": "中证A500", "trends": [at + ",5000,5001,5002,4999,100,500000,5000"]}}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return raw

    calls = []

    def fake_get(url, *args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    packet = run_worker("minute", "000510", 1, 1)
    assert packet["name"] == "中证A500"
    assert packet["rows"] == [{"time": at + ":00", "close": 5001.0}]
    assert packet["identity_origin"] == "same_minute_response"
    assert calls[0]["timeout"] == (4, 8)
    assert calls[0]["params"]["secid"] == "1.000510"


def test_verified_tls_transport_fallback_preserves_identity(monkeypatch):
    import requests
    from curl_cffi import requests as curl_requests
    raw = {"data": {"code": "399673", "name": "创业板50", "trends": ["2026-10-09 14:30,3000,3001,3002,2999,10,30000,3000"]}}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return raw

    def fail(*args, **kwargs):
        raise requests.exceptions.SSLError("test")

    def browser_get(url, *args, **kwargs):
        assert kwargs["impersonate"] == "chrome"
        assert kwargs.get("verify", True) is True
        return Response()

    monkeypatch.setattr(requests, "get", fail)
    monkeypatch.setattr(curl_requests, "get", browser_get)
    packet = run_worker("minute", "399673", 0, 1)
    assert packet["code"] == "399673"
    assert packet["transport"] == "curl_cffi_chrome"


def test_worker_timeout_is_structured_without_exception_text(monkeypatch):
    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired("hidden credential", 35)

    monkeypatch.setattr(subprocess, "run", expired)
    result = worker_call("calendar")
    assert result["error"] == "worker_timeout"
    assert "hidden credential" not in json.dumps(result)


def test_collection_checks_time_after_network_and_replaces_old_json(tmp_path):
    times = iter([timestamp("2026-10-09 14:30"), timestamp("2026-10-09 14:36")])

    class Provider:
        def calendar(self):
            return {"dates": ["2026-10-08", "2026-10-09", "2026-10-12"]}

        def minutes(self, spec, now=None):
            return {"code": spec.code, "name": spec.name, "secid": f"{spec.markets[0]}.{spec.code}", "identity_origin": "same_minute_response", "period_minutes": 1, "rows": [{"time": "2026-10-09 14:30", "close": 1.0}]}

    document, _ = collect(Provider(), clock=lambda: next(times))
    assert not document["live_usable_at_collection"]
    destination = tmp_path / "latest.json"
    destination.write_text('{"status":"old success"}', encoding="utf-8")
    write_json(destination, document)
    assert json.loads(destination.read_text(encoding="utf-8"))["status"] == "unavailable"


def test_market_closed_skips_quote_network():
    class Provider:
        def calendar(self):
            return {"dates": ["2026-09-30", "2026-10-08"]}

        def minutes(self, spec, now=None):
            raise AssertionError("Quotes must not be requested on holiday")

    document, raw = collect(Provider(), clock=lambda: timestamp("2026-10-01 14:30"))
    assert document["status"] == "market_closed"
    assert raw["packets"] == {}


def test_openapi_generation_and_read_only_operations(tmp_path):
    root = Path(__file__).parents[1]
    path = tmp_path / "openapi.json"
    subprocess.run([sys.executable, str(root / "scripts/make_openapi.py"), "https://raw.githubusercontent.com/example/repository/main/public/latest.json", "--output", str(path)], check=True)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["openapi"] == "3.1.0"
    assert all(list(p) == ["get"] for p in document["paths"].values())
    assert document["servers"][0]["url"].endswith("/public")


def test_workflows_parse_and_daily_clock_is_utc():
    import yaml
    root = Path(__file__).parents[1]
    collector = yaml.load((root / ".github/workflows/collect.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert collector["on"]["schedule"][0]["cron"] == "23 6 * * 1-5"
    assert collector["permissions"] == {"contents": "write"}
    assert "--wait --retry-window" in collector["jobs"]["collect"]["steps"][5]["run"]


def test_public_transport_success_does_not_imply_live_or_chatgpt_success(monkeypatch, tmp_path):
    import requests
    document = build_snapshot({}, {}, timestamp("2026-10-09 16:30"))

    class Response:
        status_code = 200
        headers = {"Content-Type": "text/plain"}

        def raise_for_status(self):
            pass

        def json(self):
            return document

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    destination = tmp_path / "verification.json"
    result = verify_url(Namespace(url="https://example.com/latest.json", output=str(destination), expected=None))
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert result == 0
    assert report["public_https_verified"] is True
    assert report["schema_verified"] is True
    assert report["live_usable_at_read"] is False
    assert "malformed_snapshot" not in report["reasons"]
    assert report["chatgpt_action_verified"] is False


def test_cached_old_published_document_fails_content_match(monkeypatch, tmp_path):
    import requests
    document = build_snapshot({}, {}, timestamp("2026-10-09 16:30"))
    old = build_snapshot({}, {}, timestamp("2026-10-08 16:30"))

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def raise_for_status(self):
            pass

        def json(self):
            return old

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    expected = tmp_path / "expected.json"
    write_json(expected, document)
    destination = tmp_path / "verification.json"
    assert verify_url(Namespace(url="https://example.com/latest.json", output=str(destination), expected=str(expected))) == 2
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["published_snapshot_matches"] is False
    assert report["public_https_verified"] is False


def test_non_https_or_embedded_credentials_rejected(tmp_path):
    for address in ("http://example.com/latest.json", "https://name:password@example.com/latest.json"):
        path = tmp_path / "report.json"
        assert verify_url(Namespace(url=address, output=str(path), expected=None)) == 2
        assert "password" not in path.read_text(encoding="utf-8")
