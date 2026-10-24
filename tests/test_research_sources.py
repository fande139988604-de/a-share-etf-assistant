from __future__ import annotations

from datetime import datetime
import hashlib
import json
import subprocess
import time

import pytest

from etf_assistant.core import TZ
from etf_assistant import research_sources as sources


def response(code="000510", name="中证A500", market=1, rows=None):
    return json.dumps({"rc": 0, "data": {"code": code, "name": name, "market": market,
                      "klines": rows if rows is not None else ["2026-10-22,100,101,103,99,200,3000,4,1,1,1", "2026-10-23,101,102,104,100,210,3200,4,0.99,1,1"]}}, ensure_ascii=False).encode()


def parse(raw=None, cutoff="2026-10-23T14:33:00+08:00", **kwargs):
    return sources.parse_daily_response(raw if raw is not None else response(), code="000510", market=1,
                                        url="https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000510",
                                        received_at="2026-10-23T14:32:59+08:00", as_of=datetime.fromisoformat(cutoff), **kwargs)


def test_intraday_excludes_today_final_close_and_volume():
    result = parse()
    assert [row["date"] for row in result["daily"]] == ["2026-10-22"]
    assert result["source"]["excluded_incomplete_bars"] == 1
    assert result["source"]["source_timestamp"] == "2026-10-22T15:00:00+08:00"
    assert result["source"]["status"] == "ok"


@pytest.mark.parametrize("cutoff,expected", [("2026-10-23T06:33:00+00:00", 1),
                                              ("2026-10-23T06:59:59+00:00", 1),
                                              ("2026-10-23T07:00:00+00:00", 2),
                                              ("2026-10-24T14:33:00+08:00", 2)])
def test_exchange_close_boundary_and_utc_conversion(cutoff, expected):
    result = parse(cutoff=cutoff)
    assert len(result["daily"]) == expected
    assert result["source"]["decision_cutoff"].endswith("+08:00")


@pytest.mark.parametrize("raw", [response(code="399673"), response(name="上证指数"), response(market=2),
                                    json.dumps({"rc": 0, "data": {"klines": []}}).encode()])
def test_same_body_identity_required(raw):
    result = parse(raw)
    assert result["source"]["reason"] == "daily_identity_mismatch"
    assert not result["source"]["identity_verified"]
    assert result["daily"] == []


def test_raw_body_hash_and_http_status_are_preserved():
    raw = response()
    result = parse(raw, http_status=503)
    assert result["source"]["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["source"]["http_status"] == 503
    assert result["source"]["reason"] == "http_status_not_200"
    assert result["daily"] == []


@pytest.mark.parametrize("rows,reason", [
    (["2026-10-26,100,101,103,99,200,3000,4,1,1,1"], "future_daily_date"),
    (["2026-10-22,100,101,103,99,200,3000,4,1,1,1"] * 2, "daily_date_invalid_or_duplicate"),
    (["2026-10-22,100,105,103,99,200,3000,4,1,1,1"], "daily_ohlcv_invalid"),
    (["2026-10-22,100,nan,103,99,200,3000,4,1,1,1"], "daily_ohlcv_invalid"),
    (["2026-10-22,100,101,103,99,-1,3000,4,1,1,1"], "daily_ohlcv_invalid"),
    (["2026-10-24,100,101,103,99,200,3000,4,1,1,1"], "daily_date_invalid_or_duplicate"),
    (["2026-10-22,100"], "daily_row_malformed"),
])
def test_invalid_or_future_records_fail_closed(rows, reason):
    result = parse(response(rows=rows))
    assert result["source"]["reason"] == reason
    assert result["source"]["status"] == "unavailable"
    assert result["daily"] == []


def test_malformed_json_and_empty_history_unavailable():
    assert parse(b"<html>bad gateway</html>")["source"]["reason"] == "daily_response_invalid"
    assert parse(response(rows=[]))["source"]["reason"] == "daily_history_empty"


def test_sorted_rows_and_all_rows_reference_exact_source():
    rows = ["2026-10-23,101,102,104,100,210,3200,4,0.99,1,1", "2026-10-22,100,101,103,99,200,3000,4,1,1,1"]
    result = parse(response(rows=rows), cutoff="2026-10-24T14:33:00+08:00")
    assert [row["date"] for row in result["daily"]] == ["2026-10-22", "2026-10-23"]
    assert all(row["source_id"] == result["source"]["source_id"] for row in result["daily"])


def test_only_two_index_identities_supported():
    with pytest.raises(ValueError, match="unsupported"):
        sources.daily_url("000300", 1, datetime.now(TZ))
    with pytest.raises(ValueError, match="unsupported"):
        sources.daily_url("399673", 1, datetime.now(TZ))


def test_hard_worker_timeout_becomes_gap(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 1
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(sources.subprocess, "run", timeout)
    result = sources._bounded_worker("000510", datetime.now(TZ), 1)
    assert result["source"]["reason"] == "research_collection_timeout"
    assert result["daily"] == []


def test_parallel_collection_preserves_missing_fields_and_cutoff(monkeypatch):
    def worker(command, cutoff, budget):
        assert budget == 1
        time.sleep(0.03)
        if command == "calendar":
            return {"error": "source_call_failed", "received_at": cutoff.isoformat()}
        return {"daily": [], "source": {"status": "unavailable", "reason": "transport_failed"}}
    monkeypatch.setattr(sources, "_bounded_worker", worker)
    start = time.monotonic()
    bundle = sources.collect_sources(datetime(2026, 10, 23, 14, 33, tzinfo=TZ), 1)
    assert time.monotonic() - start < 0.3
    assert set(bundle["indices"]) == {"000510", "399673"}
    assert bundle["breadth"]["status"] == bundle["etf_flows"]["status"] == "unavailable"
    assert bundle["calendar"]["dates"] == []
    assert bundle["calendar"]["status"] == "unavailable"
    assert bundle["as_of"] == "2026-10-23T14:33:00+08:00"


@pytest.mark.parametrize("budget", [0, 51])
def test_collection_budget_restricted(budget):
    with pytest.raises(ValueError):
        sources.collect_sources(max_seconds=budget)


def test_optional_curated_input_kept_separate_and_rejects_personal_fields(tmp_path):
    path = tmp_path / "curated.json"
    packet = {"catalysts": [{"event_id": "official-event", "source": {"url": "https://www.sse.com.cn/"}}]}
    path.write_text(json.dumps(packet), encoding="utf-8")
    assert sources.load_curated_evidence(path) == packet
    path.write_text(json.dumps({"breadth": {"observations": [{"holdings": 100}]}}), encoding="utf-8")
    with pytest.raises(ValueError, match="personal_investment_fields_forbidden"):
        sources.load_curated_evidence(path)
    path.write_text(json.dumps({"portfolio": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported_curated_fields"):
        sources.load_curated_evidence(path)
