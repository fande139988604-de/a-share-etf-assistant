from copy import deepcopy
from datetime import timedelta

from etf_assistant.core import INSTRUMENTS, build_snapshot, timestamp
from etf_assistant.risk import assess_risk_at_read, expected_times

NOW = timestamp("2026-10-09 14:30")
CALENDAR = {"dates": ["2026-10-08", "2026-10-09", "2026-10-12"]}


def packets(period=1):
    times = sorted(expected_times(NOW.date(), NOW, period))
    return {spec.code: {"code": spec.code, "name": spec.name, "secid": f"{spec.markets[0]}.{spec.code}", "identity_origin": "same_minute_response", "period_minutes": period, "rows": [{"time": at.isoformat(), "close": 100.0} for at in times]} for spec in INSTRUMENTS}


def test_complete_flat_day_has_no_trigger_but_no_sell_instruction():
    doc = build_snapshot(CALENDAR, packets(), NOW)
    assert doc["risk_analysis"]["status"] == "ok"
    assert [r["level"] for r in doc["risk_analysis"]["indices"]] == ["no_trigger", "no_trigger"]
    assert doc["risk_analysis"]["sell_order"] is None
    assert doc["risk_analysis"]["backtested"] is False
    assert assess_risk_at_read(doc, NOW)["risk_usable_at_read"]


def test_sharp_fall_triggers_three_explained_observations():
    data = packets()
    for packet in data.values():
        packet["rows"][-1]["close"] = 98.0
    doc = build_snapshot(CALENDAR, data, NOW)
    for risk in doc["risk_analysis"]["indices"]:
        assert risk["level"] == "elevated"
        assert risk["triggers"] == ["peak_drawdown", "fast_decline", "morning_low_break"]
        assert abs(risk["metrics"]["return_20m_pct"] + 2) < 1e-10
        assert risk["as_of"] == NOW.isoformat()


def test_one_trigger_is_watch_not_a_sell_order():
    data = packets()
    for packet in data.values():
        packet["rows"][0]["close"] = 102.0
    doc = build_snapshot(CALENDAR, data, NOW)
    assert all(r["level"] == "watch" and r["triggers"] == ["peak_drawdown"] for r in doc["risk_analysis"]["indices"])


def test_missing_history_never_looks_like_no_risk():
    data = packets()
    data["000510"]["rows"].pop(10)
    doc = build_snapshot(CALENDAR, data, NOW)
    assert doc["status"] == "ok"  # Current quote can be valid while history is incomplete.
    assert doc["risk_analysis"]["status"] == "unavailable"
    assert doc["risk_analysis"]["indices"][0]["reasons"] == ["risk_history_incomplete"]
    assert not assess_risk_at_read(doc, NOW)["risk_usable_at_read"]


def test_bad_history_prices_and_conflicting_old_bars_reject_analysis():
    for change in ("bad_price", "duplicate"):
        data = packets()
        if change == "bad_price":
            data["000510"]["rows"][5]["close"] = float("nan")
        else:
            extra = dict(data["000510"]["rows"][5], close=90)
            data["000510"]["rows"].append(extra)
        doc = build_snapshot(CALENDAR, data, NOW)
        assert doc["risk_analysis"]["status"] == "unavailable"


def test_stale_closed_fixture_or_wrong_identity_never_emit_risk_metrics():
    cases = [
        build_snapshot(CALENDAR, packets(), NOW + timedelta(minutes=6)),
        build_snapshot(CALENDAR, packets(), NOW, data_kind="offline_fixture"),
        build_snapshot({"dates": ["2026-09-30", "2026-10-08"]}, packets(), timestamp("2026-10-01 14:30")),
    ]
    wrong = packets()
    wrong["399673"]["code"] = "399006"
    cases.append(build_snapshot(CALENDAR, wrong, NOW))
    for doc in cases:
        assert doc["risk_analysis"]["status"] == "unavailable"
        assert all(r["metrics"] is None and r["triggers"] == [] for r in doc["risk_analysis"]["indices"])


def test_five_minute_bars_use_same_twenty_minute_reference():
    doc = build_snapshot(CALENDAR, packets(period=5), NOW)
    assert doc["risk_analysis"]["status"] == "ok"
    for risk in doc["risk_analysis"]["indices"]:
        assert risk["metrics"]["reference_20m_timestamp"] == "2026-10-09T14:10:00+08:00"
        assert risk["metrics"]["bar_period_minutes"] == 5


def test_future_close_is_not_used_in_risk():
    data = packets()
    data["000510"]["rows"].append({"time": "2026-10-09 14:31", "close": 1})
    doc = build_snapshot(CALENDAR, data, NOW)
    assert doc["status"] == "unavailable"
    assert all(r["metrics"] is None for r in doc["risk_analysis"]["indices"])


def test_cached_or_timestamp_mismatched_analysis_fails_at_read():
    doc = build_snapshot(CALENDAR, packets(), NOW)
    assert not assess_risk_at_read(doc, NOW + timedelta(minutes=6))["risk_usable_at_read"]
    changed = deepcopy(doc)
    changed["risk_analysis"]["indices"][0]["as_of"] = "2026-10-09T11:30:00+08:00"
    assert not assess_risk_at_read(changed, NOW)["risk_usable_at_read"]
