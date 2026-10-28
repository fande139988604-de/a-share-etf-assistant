"""Regressions for Eastmoney's forward-labelled, still-forming minute bar."""
from copy import deepcopy
from datetime import timedelta

import pytest

from etf_assistant import provider
from etf_assistant.core import INSTRUMENTS, build_snapshot, validate_quote
from etf_assistant.risk import assess_risk_at_read
from test_risk import CALENDAR, NOW, packets


def open_bar_packet(code="000510"):
    packet = deepcopy(packets()[code])
    packet["received_at"] = (NOW + timedelta(seconds=10)).isoformat()
    future = {"time": (NOW + timedelta(minutes=1)).isoformat(), "close": 101.25}
    packet["rows"].append(future)
    packet["raw_response"] = {"data": {"code": packet["code"], "name": packet["name"],
                              "trends": [f'{future["time"]},100,101.25,102,99,10']}}
    return packet


def test_only_open_next_minute_is_excluded_and_evidence_preserved():
    original = open_bar_packet()
    result = provider.completed_minute_packet(original)
    assert result is not original
    assert original["rows"][-1]["time"].endswith("14:31:00+08:00")
    assert result["rows"] == original["rows"][:-1]
    assert result["raw_response"] == original["raw_response"]
    assert result["response_sha256"] == original["response_sha256"]
    assert result["received_at"] == original["received_at"]
    assert result["excluded_incomplete_bar"] == {
        "timestamp": "2026-10-09T14:31:00+08:00",
        "reason": "next_minute_in_progress_at_receipt",
    }
    quote = validate_quote(INSTRUMENTS[0], result, NOW + timedelta(seconds=10), True)
    assert quote["status"] == "ok"
    assert quote["source_timestamp"] == NOW.isoformat()
    assert quote["age_seconds"] == 10
    assert quote["source"]["excluded_incomplete_bar"] == result["excluded_incomplete_bar"]


@pytest.mark.parametrize("mutation", [
    "far_future", "multiple_future", "duplicate", "unordered",
    "five_minute", "bool_period", "missing_raw", "wrong_raw_code",
    "wrong_raw_close", "wrong_raw_time",
])
def test_abnormal_future_data_is_never_silently_trimmed(mutation):
    packet = open_bar_packet()
    if mutation == "far_future":
        packet["rows"][-1]["time"] = (NOW + timedelta(minutes=2)).isoformat()
    elif mutation == "multiple_future":
        packet["rows"].append({"time": (NOW + timedelta(minutes=2)).isoformat(), "close": 102})
    elif mutation == "duplicate":
        packet["rows"].insert(-1, deepcopy(packet["rows"][-2]))
    elif mutation == "unordered":
        packet["rows"][0], packet["rows"][1] = packet["rows"][1], packet["rows"][0]
    elif mutation == "five_minute":
        packet["period_minutes"] = 5
    elif mutation == "bool_period":
        packet["period_minutes"] = True
    elif mutation == "missing_raw":
        del packet["raw_response"]
    elif mutation == "wrong_raw_code":
        packet["raw_response"]["data"]["code"] = "000905"
    elif mutation == "wrong_raw_close":
        packet["raw_response"]["data"]["trends"][-1] = "2026-10-09 14:31,100,105,102,99,10"
    else:
        packet["raw_response"]["data"]["trends"][-1] = "2026-10-09 14:32,100,101.25,102,99,10"
    assert provider.completed_minute_packet(packet) is packet
    quote = validate_quote(INSTRUMENTS[0], packet, NOW + timedelta(seconds=10), True)
    assert not quote["valid_at_collection"]


def test_future_at_receipt_is_rejected_even_when_now_has_advanced():
    packet = open_bar_packet()
    quote = validate_quote(INSTRUMENTS[0], packet, NOW + timedelta(minutes=1, seconds=30), True)
    assert quote["status"] == "future_source_timestamp"


def test_provider_reuses_only_previously_valid_packet_and_rechecks_age(monkeypatch):
    clock = [NOW + timedelta(seconds=10)]
    available = [True]
    received = open_bar_packet("000510")["received_at"]

    def fake_worker(*args, **kwargs):
        if available[0]:
            return open_bar_packet("000510")
        return {"error": "source_call_failed", "error_type": "ConnectionError"}

    monkeypatch.setattr(provider, "worker_call", fake_worker)
    service = provider.AKShareProvider()
    first = service.minutes(INSTRUMENTS[0], now=lambda: clock[0])
    assert first["rows"][-1]["time"] == NOW.isoformat()
    available[0] = False
    clock[0] = NOW + timedelta(seconds=80)
    second = service.minutes(INSTRUMENTS[0], now=lambda: clock[0])
    assert second["reused_previous_valid_packet"] is True
    assert second["received_at"] == received
    assert second["response_sha256"] == first["response_sha256"]
    quote = validate_quote(INSTRUMENTS[0], second, clock[0], True)
    assert quote["age_seconds"] == 80
    clock[0] = NOW + timedelta(minutes=5, seconds=1)
    stale = service.minutes(INSTRUMENTS[0], now=lambda: clock[0])
    assert stale["error"] == "all_minute_sources_unavailable"


def test_normalized_two_index_packets_still_require_complete_risk_history():
    source = {code: provider.completed_minute_packet(open_bar_packet(code))
              for code in ("000510", "399673")}
    now = NOW + timedelta(seconds=10)
    document = build_snapshot(CALENDAR, source, now)
    assert document["status"] == "ok"
    assert document["risk_analysis"]["status"] == "ok"
    assert assess_risk_at_read(document, now)["risk_usable_at_read"]
    source["000510"]["rows"].pop(10)
    incomplete = build_snapshot(CALENDAR, source, now)
    assert incomplete["risk_analysis"]["status"] == "unavailable"
