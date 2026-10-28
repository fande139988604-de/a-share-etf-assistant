from argparse import Namespace
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import threading

import pytest

from etf_assistant import cli, provider
from etf_assistant.core import INSTRUMENTS, assess_public_snapshot, build_snapshot, timestamp, validate_quote
from etf_assistant.publication import extract_snapshot
from etf_assistant.risk import assess_risk_at_read
from scripts.prepare_publication import prepare
from scripts.check_delivery import check
from scripts.render_snapshot import render_snapshot
from test_risk import CALENDAR, NOW, packets


@pytest.mark.parametrize("spec", INSTRUMENTS)
@pytest.mark.parametrize("period", [1, 5])
@pytest.mark.parametrize("wrong", [False, True])
def test_actual_parser_uses_identity_from_same_response_for_both_indices(monkeypatch, spec, period, wrong):
    import requests
    from etf_assistant.worker import run_worker
    from etf_assistant.core import identity_matches
    code = ("000905" if spec.code == "000510" else "399006") if wrong else spec.code
    name = ("中证500" if spec.code == "000510" else "创业板指") if wrong else spec.name
    fields = ",100,100,100,100,10,1000,100" if period == 1 else ",100,100,100,100,10,1000,0,0,0,0"
    raw = {"data": {"code": code, "name": name, "trends" if period == 1 else "klines": ["2026-10-09 14:30" + fields]}}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return raw

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    packet = run_worker("minute", spec.code, spec.markets[0], period)
    assert packet["code"] == code and packet["name"] == name
    assert packet["raw_response"] == raw
    assert identity_matches(spec, packet) is not wrong
    assert packet["identity_origin"] == "same_minute_response"


def test_incomplete_one_minute_history_tries_complete_five_minute(monkeypatch):
    one = packets()["399673"]
    one["rows"].pop(5)
    five = packets(5)["399673"]
    calls = []

    def worker(*args, **kwargs):
        calls.append(args)
        return deepcopy(one if args[-1] == "1" else five)

    monkeypatch.setattr(provider, "worker_call", worker)
    result = provider.AKShareProvider().minutes(INSTRUMENTS[1], now=lambda: NOW)
    assert result["period_minutes"] == 5
    assert len(calls) == 2
    assert result["attempts"][0]["risk_reasons"] == ["risk_history_incomplete"]


def test_provider_deadline_stops_calls_without_changing_freshness(monkeypatch):
    monkeypatch.setattr(provider, "shanghai_now", lambda: NOW)
    monkeypatch.setattr(provider, "worker_call", lambda *a, **k: pytest.fail("network after deadline"))
    result = provider.AKShareProvider(deadline=NOW).minutes(INSTRUMENTS[0], now=lambda: NOW)
    assert result["error"] == "collection_deadline_exceeded"


def test_remaining_deadline_limits_worker_timeout(monkeypatch):
    monkeypatch.setattr(provider, "shanghai_now", lambda: NOW)
    timeouts = []

    def worker(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return packets()["399673"]

    monkeypatch.setattr(provider, "worker_call", worker)
    provider.AKShareProvider(deadline=NOW + timedelta(seconds=3)).minutes(INSTRUMENTS[1], now=lambda: NOW)
    assert timeouts == [3]


def test_collection_fetches_both_indices_concurrently():
    rendezvous = threading.Barrier(2, timeout=5)

    class Provider:
        def calendar(self):
            return CALENDAR

        def minutes(self, spec, now=None):
            rendezvous.wait()
            return packets()[spec.code]

    document, _ = cli.collect(Provider(), clock=lambda: NOW)
    assert document["risk_analysis"]["status"] == "ok"


def test_calendar_reused_only_on_same_day(monkeypatch):
    now = [NOW]
    calls = []
    monkeypatch.setattr(provider, "shanghai_now", lambda: now[0])

    def worker(*args):
        calls.append(args)
        return dict(CALENDAR, received_at=now[0].isoformat())

    monkeypatch.setattr(provider, "worker_call", worker)
    client = provider.AKShareProvider()
    client.calendar()
    client.calendar()
    assert len(calls) == 1
    now[0] += timedelta(days=1)
    client.calendar()
    assert len(calls) == 2


def test_quote_success_with_missing_risk_retries_and_then_succeeds(monkeypatch, tmp_path):
    bad = packets()
    bad["000510"]["rows"].pop(0)
    documents = [build_snapshot(CALENDAR, bad, NOW), build_snapshot(CALENDAR, packets(), NOW)]
    monkeypatch.setattr(cli, "shanghai_now", lambda: NOW)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cli.AKShareProvider, "calendar", lambda self: CALENDAR)
    calls = []

    def collect(*a, **k):
        calls.append(1)
        return documents.pop(0), {"packets": {}}

    monkeypatch.setattr(cli, "collect", collect)
    args = Namespace(wait=False, retry_window=True, capture_seconds=120, output=str(tmp_path / "latest.json"), evidence=str(tmp_path / "raw.json"))
    assert cli.collect_command(args) == 0
    assert len(calls) == 2
    assert len(json.loads((tmp_path / "raw.json").read_text())["collection_attempts"]) == 2


def test_incomplete_risk_fails_collection_exit_code(monkeypatch, tmp_path):
    bad = packets()
    bad["000510"]["rows"].pop(0)
    monkeypatch.setattr(cli, "shanghai_now", lambda: NOW)
    monkeypatch.setattr(cli.AKShareProvider, "calendar", lambda self: CALENDAR)
    monkeypatch.setattr(cli, "collect", lambda *a, **k: (build_snapshot(CALENDAR, bad, NOW), {"packets": {}}))
    args = Namespace(wait=False, retry_window=False, capture_seconds=120, output=str(tmp_path / "latest.json"), evidence=None)
    assert cli.collect_command(args) == 2


def test_early_warmup_waits_for_actual_target_without_rewriting_time(monkeypatch, tmp_path):
    now = [NOW - timedelta(minutes=73)]
    monkeypatch.setattr(cli, "shanghai_now", lambda: now[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + timedelta(seconds=seconds)))
    monkeypatch.setattr(cli.AKShareProvider, "calendar", lambda self: CALENDAR)

    def collect(*a, **k):
        assert now[0] == NOW
        return build_snapshot(CALENDAR, packets(), now[0]), {"packets": {}}

    monkeypatch.setattr(cli, "collect", collect)
    args = Namespace(wait=True, retry_window=False, capture_seconds=120, output=str(tmp_path / "latest.json"), evidence=None)
    assert cli.collect_command(args) == 0
    doc = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert doc["generated_at"] == NOW.isoformat()
    assert doc["collection"]["started_at"] == (NOW - timedelta(minutes=73)).isoformat()


@pytest.mark.parametrize("mutation", ["missing_minute", "conflicting_old_bar", "off_grid_bar", "future_next_day", "wrong_identity", "nan", "boolean", "future_receipt"])
def test_invalid_history_or_identity_cannot_produce_ok_risk(mutation):
    data = packets()
    packet = data["000510"]
    if mutation == "missing_minute":
        packet["rows"].pop(3)
    elif mutation == "conflicting_old_bar":
        packet["rows"].append(dict(packet["rows"][0], close=1))
    elif mutation == "off_grid_bar":
        packet["rows"].append({"time": "2026-10-09T10:00:01+08:00", "close": 1})
    elif mutation == "future_next_day":
        packet["rows"].append({"time": "2026-10-10T14:30:00+08:00", "close": 1})
    elif mutation == "wrong_identity":
        packet["identity_origin"] = "separate_quote_response"
    elif mutation == "future_receipt":
        packet["received_at"] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        packet["rows"][1]["close"] = float("nan") if mutation == "nan" else True
    assert build_snapshot(CALENDAR, data, NOW)["risk_analysis"]["status"] == "unavailable"


@pytest.mark.parametrize("mutation", ["level", "triggers", "metric", "nonfinite", "reference", "count", "period", "rules", "expiry", "generated", "contract"])
def test_read_gate_rejects_tampered_risk_and_time_contract(mutation):
    doc = build_snapshot(CALENDAR, packets(), NOW)
    risk = doc["risk_analysis"]["indices"][0]
    if mutation == "level":
        risk["level"] = "watch"
    elif mutation == "triggers":
        risk["triggers"] = ["fast_decline"]
    elif mutation in ("metric", "nonfinite"):
        risk["metrics"]["return_20m_pct"] = 42 if mutation == "metric" else float("nan")
    elif mutation == "reference":
        risk["metrics"]["reference_20m_timestamp"] = NOW.isoformat()
    elif mutation == "count":
        risk["metrics"]["sample_count"] = 1
    elif mutation == "period":
        risk["metrics"]["bar_period_minutes"] = 15
    elif mutation == "rules":
        doc["risk_analysis"]["rules"][0]["threshold_pct"] = 90
    elif mutation == "expiry":
        doc["indices"][0]["expires_at"] = (NOW + timedelta(hours=1)).isoformat()
    elif mutation == "generated":
        doc["generated_at"] = (NOW - timedelta(seconds=1)).isoformat()
    else:
        doc["max_age_seconds"] = 3600
    assert not assess_risk_at_read(doc, NOW)["risk_usable_at_read"]


def test_publication_rejects_old_checkout_and_fixture(tmp_path):
    source = tmp_path / "source.json"
    doc = build_snapshot(CALENDAR, packets(), NOW)
    cli.write_json(source, doc)
    with pytest.raises(ValueError, match="not_this_run"):
        prepare(source, tmp_path / "published", NOW + timedelta(seconds=1), NOW + timedelta(seconds=2))
    cli.write_json(source, build_snapshot(CALENDAR, packets(), NOW, data_kind="offline_fixture"))
    with pytest.raises(ValueError, match="not_this_run"):
        prepare(source, tmp_path / "published", NOW, NOW)
    assert not (tmp_path / "published/latest.json").exists()


def test_publication_json_html_equal_and_expired_success_rejected(tmp_path):
    source = tmp_path / "source.json"
    doc = build_snapshot(CALENDAR, packets(), NOW)
    cli.write_json(source, doc)
    target = tmp_path / "published"
    prepare(source, target, NOW, NOW)
    assert extract_snapshot((target / "snapshot.html").read_text(encoding="utf-8")) == doc
    with pytest.raises(ValueError, match="expired"):
        prepare(source, target, NOW, NOW + timedelta(minutes=6))


def test_html_verification_rechecks_risk_and_busts_cache(monkeypatch, tmp_path):
    import requests
    doc = build_snapshot(CALENDAR, packets(), NOW)
    calls = []

    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html", "Age": "600"}
        text = render_snapshot(doc)
        content = text.encode("utf-8")

        def raise_for_status(self):
            pass

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(requests, "get", fetch)
    monkeypatch.setattr(cli, "shanghai_now", lambda: NOW)
    args = Namespace(url="https://example.com/snapshot.html", expected=None, output=str(tmp_path / "verify.json"), require_live=True)
    assert cli.verify_url(args) == 0
    assert "read_at" in calls[0]["params"]
    assert calls[0]["headers"]["Cache-Control"] == "no-cache"
    monkeypatch.setattr(cli, "shanghai_now", lambda: NOW + timedelta(minutes=6))
    assert cli.verify_url(args) == 2
    report = json.loads((tmp_path / "verify.json").read_text())
    assert report["public_https_verified"] and not report["risk_usable_at_read"]


def test_ambiguous_html_and_transport_only_delivery_rejected():
    with pytest.raises(ValueError):
        extract_snapshot('<pre id="snapshot-json">{}</pre><pre id="snapshot-json">{}</pre>')
    report = {"public_https_verified": True, "published_snapshot_matches": True, "live_usable_at_read": False, "risk_usable_at_read": False}
    with pytest.raises(ValueError, match="fresh_risk"):
        check({"status": "ok"}, [report])
    check({"status": "market_closed"}, [report])


def test_utc_and_shanghai_identical_for_complete_risk():
    data = packets()
    for packet in data.values():
        for row in packet["rows"]:
            from datetime import timezone
            row["time"] = timestamp(row["time"]).astimezone(timezone.utc).isoformat()
    doc = build_snapshot(CALENDAR, data, timestamp("2026-10-09T06:30:00Z"))
    assert doc["risk_analysis"]["status"] == "ok"
    assert assess_risk_at_read(doc, NOW)["risk_usable_at_read"]
