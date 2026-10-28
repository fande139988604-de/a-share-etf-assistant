"""Regressions for transport, syntax, completeness and live-data failures."""
from argparse import Namespace
from copy import deepcopy
from datetime import timedelta
from html import escape
import json
import socket

import pytest
import requests

from etf_assistant import cli
from etf_assistant.core import build_snapshot
from etf_assistant.publication import SnapshotBlockError, extract_snapshot
from scripts.render_snapshot import render_snapshot
from test_risk import CALENDAR, NOW, packets


def verify(monkeypatch, tmp_path, body, *, url="https://example.org/latest.json", status=200, headers=None, now=NOW, require_live=False):
    headers_ = headers
    class Response:
        status_code = status
        content = body
        headers = headers_ if headers_ is not None else {"Content-Type": "application/json"}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(cli, "shanghai_now", lambda: now)
    out = tmp_path / "result.json"
    result = cli.verify_url(Namespace(url=url, expected=None, output=str(out), require_live=require_live))
    return result, json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("body,category,reason", [
    (b'{"x":1', "json_syntax", "invalid_json_syntax"),
    (b'{"x":1,}', "json_syntax", "invalid_json_syntax"),
    (b'<html>gateway error</html>', "json_syntax", "html_response_for_json"),
    (b'\xef\xbb\xbf{}', "encoding", "utf8_bom_present"),
    (b'"\xff"', "encoding", "invalid_utf8"),
    (b'{"x":NaN}', "json_integrity", "nonstandard_json_number"),
    (b'{"x":1,"x":2}', "json_integrity", "duplicate_json_key"),
    (b'{}', "schema", "snapshot_schema_invalid"),
])
def test_bad_responses_are_classified_at_the_right_layer(monkeypatch, tmp_path, body, category, reason):
    result, report = verify(monkeypatch, tmp_path, body)
    assert result == 2
    assert report["http_verified"]
    assert report["failure_category"] == category
    assert reason in report["reasons"]
    assert report["json_parsed"] is (category == "schema")
    assert not report["public_https_verified"]


def test_http_status_and_incomplete_body_are_not_json_errors(monkeypatch, tmp_path):
    result, report = verify(monkeypatch, tmp_path, b'<html>error</html>', status=503)
    assert result == 2 and report["failure_category"] == "http"
    assert not report["http_verified"] and not report["json_parsed"]
    result, report = verify(monkeypatch, tmp_path, b'{}', headers={"Content-Length": "99"})
    assert result == 2 and report["reasons"] == ["response_length_mismatch"]


@pytest.mark.parametrize("exception,reason", [
    (requests.ConnectionError(socket.gaierror(-2, "not known")), "dns_error"),
    (requests.ConnectionError("connection failed"), "http_connection_error"),
    (requests.Timeout("timed out"), "http_timeout"),
    (requests.exceptions.SSLError("tls failed"), "tls_error"),
])
def test_network_failures_do_not_claim_json_parse_errors(monkeypatch, tmp_path, exception, reason):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(requests, "get", fail)
    out = tmp_path / "result.json"
    assert cli.verify_url(Namespace(url="https://example.org/latest.json", expected=None, output=str(out))) == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["failure_category"] == "transport" and report["reasons"] == [reason]
    assert not report["json_parsed"]


def test_escaped_html_round_trips_json_and_chinese_text(monkeypatch, tmp_path):
    document = build_snapshot(CALENDAR, packets(), NOW)
    document["consumer_rule"] = '中文 & </pre><script>alert("x")</script> \\ \n "'
    page = render_snapshot(document)
    assert extract_snapshot(page) == document
    assert "<script>" not in page
    result, report = verify(monkeypatch, tmp_path, page.encode("utf-8"), url="https://example.org/snapshot.html")
    assert result == 0 and report["json_parsed"] and report["schema_verified"]


@pytest.mark.parametrize("page", [
    '<pre id="snapshot-json">{}</pre><pre id="snapshot-json">{}</pre>',
    '<pre id="snapshot-json">{}',
    '<pre id="snapshot-json"><b>{}</b></pre>',
    '<pre id="snapshot-json">{}<!-- hidden --></pre>',
    '<pre id="snapshot-json" id="different">{}</pre>',
])
def test_ambiguous_truncated_or_nested_html_is_rejected(page):
    with pytest.raises(SnapshotBlockError):
        extract_snapshot(page)


def test_bad_embedded_json_is_not_misclassified_as_html(monkeypatch, tmp_path):
    page = '<pre id="snapshot-json">' + escape('{"x":') + '</pre>'
    result, report = verify(monkeypatch, tmp_path, page.encode(), url="https://example.org/snapshot.html")
    assert result == 2 and report["failure_category"] == "json_syntax"


def test_valid_json_and_schema_can_still_be_unavailable_or_stale(monkeypatch, tmp_path):
    unavailable = build_snapshot(CALENDAR, {}, NOW)
    result, report = verify(monkeypatch, tmp_path, json.dumps(unavailable).encode(), require_live=True)
    assert result == 2 and report["public_https_verified"] and report["schema_verified"]
    assert report["failure_category"] == "data_validation"
    assert report["snapshot_status"] == "unavailable" and report["risk_status"] == "unavailable"

    fresh = build_snapshot(CALENDAR, packets(), NOW)
    result, report = verify(monkeypatch, tmp_path, json.dumps(fresh).encode(), now=NOW + timedelta(seconds=301), require_live=True)
    assert result == 2 and report["failure_category"] == "data_validation"
    assert report["json_parsed"] and report["schema_verified"] and not report["live_usable_at_read"]


def test_future_timestamp_stays_unusable_in_shanghai(monkeypatch, tmp_path):
    values = deepcopy(packets())
    values["000510"]["rows"].append({"time": (NOW + timedelta(minutes=1)).isoformat(), "close": 100})
    document = build_snapshot(CALENDAR, values, NOW)
    assert document["timezone"] == "Asia/Shanghai"
    assert document["generated_at"].endswith("+08:00")
    assert document["indices"][0]["status"] == "future_source_timestamp"
    result, report = verify(monkeypatch, tmp_path, json.dumps(document).encode(), require_live=True)
    assert result == 2 and report["failure_category"] == "data_validation"
    assert "future_source_timestamp" in report["reasons"]
