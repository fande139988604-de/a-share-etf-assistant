from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from etf_assistant.core import INSTRUMENTS, assess_public_snapshot, build_snapshot, calendar_state, timestamp, validate_quote


NOW = timestamp("2026-10-09T14:30:00+08:00")
CALENDAR = {"dates": ["2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12"], "received_at": NOW.isoformat()}


def packet(spec=INSTRUMENTS[0], at="2026-10-09 14:30:00", close=5000.0):
    return {"code": spec.code, "name": spec.name, "secid": f"{spec.markets[0]}.{spec.code}", "identity_origin": "same_minute_response", "received_at": NOW.isoformat(), "period_minutes": 1, "response_sha256": "a" * 64, "rows": [{"time": at, "close": close}]}


def snapshot(now=NOW):
    return build_snapshot(CALENDAR, {spec.code: packet(spec) for spec in INSTRUMENTS}, now)


def test_current_minute_passes_for_both_indices():
    doc = snapshot()
    assert doc["status"] == "ok"
    assert doc["live_usable_at_collection"] is True
    assert assess_public_snapshot(doc, NOW)["live_usable_at_read"] is True


@pytest.mark.parametrize("at,status", [
    ("2026-10-09 11:30:00", "outside_target_window"),
    ("2026-10-08 14:30:00", "wrong_trading_date"),
    ("2026-10-09 14:30:01", "future_source_timestamp"),
    ("bad timestamp", "timestamp_missing_or_invalid"),
    ("2026-10-09 14:24:59", "outside_target_window"),
])
def test_bad_source_times_fail(at, status):
    result = validate_quote(INSTRUMENTS[0], packet(at=at), NOW, True)
    assert result["status"] == status
    assert result["quote"] is None


@pytest.mark.parametrize("change", [
    {"code": "000905", "name": "中证500"},
    {"code": "399006", "name": "创业板指"},
    {"name": "中证500"},
    {"name": None},
    {"code": "510"},
    {"secid": "0.000510"},
    {"identity_origin": "configured_name"},
])
def test_wrong_or_unproven_identity_fails(change):
    data = packet()
    data.update(change)
    assert validate_quote(INSTRUMENTS[0], data, NOW, True)["status"] == "identity_mismatch_or_missing"


def test_known_market_variant_and_name_spacing():
    data = packet()
    data.update(secid="2.000510", name="中证 Ａ５００ 指数")
    assert validate_quote(INSTRUMENTS[0], data, NOW, True)["valid_at_collection"]


def test_exact_five_minute_boundary_then_expiry():
    data = packet(at="2026-10-09 14:25:00")
    assert validate_quote(INSTRUMENTS[0], data, NOW, True)["valid_at_collection"]
    assert validate_quote(INSTRUMENTS[0], data, NOW + timedelta(seconds=1), True)["status"] == "stale_data"


def test_latency_counts_against_freshness():
    assert validate_quote(INSTRUMENTS[0], packet(), NOW + timedelta(seconds=301), True)["status"] == "stale_data"


def test_late_job_cannot_use_newer_data_as_1430():
    data = packet(at="2026-10-09 14:36:00")
    assert not validate_quote(INSTRUMENTS[0], data, NOW + timedelta(minutes=6), True)["valid_at_collection"]


def test_before_target_cannot_publish_live():
    assert validate_quote(INSTRUMENTS[0], packet(at="2026-10-09 14:29:00"), NOW - timedelta(seconds=1), True)["status"] == "before_target_window"


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), "not a price"])
def test_invalid_prices_fail(price):
    assert validate_quote(INSTRUMENTS[0], packet(close=price), NOW, True)["status"] == "invalid_price"


def test_missing_and_empty_data_fail():
    assert not validate_quote(INSTRUMENTS[0], {"error": "worker_timeout"}, NOW, True)["valid_at_collection"]
    data = packet()
    data["rows"] = []
    assert validate_quote(INSTRUMENTS[0], data, NOW, True)["status"] == "no_minute_data"


def test_conflicting_duplicates_fail():
    data = packet()
    data["rows"].append({"time": "2026-10-09 14:30:00", "close": 1.0})
    assert validate_quote(INSTRUMENTS[0], data, NOW, True)["status"] == "conflicting_duplicate_timestamp"


def test_trade_calendar_holiday_and_make_up_weekend():
    assert calendar_state(CALENDAR, date(2026, 10, 1))["is_trading_day"] is False
    assert calendar_state(CALENDAR, date(2026, 10, 10))["is_trading_day"] is False


def test_calendar_out_of_coverage_is_unknown_not_weekday_guess():
    assert calendar_state({"dates": ["2025-12-31"]}, NOW.date())["is_trading_day"] is None
    assert build_snapshot({}, {}, NOW)["status"] == "calendar_unverified"


def test_calendar_unknown_and_closed_never_pass():
    assert not validate_quote(INSTRUMENTS[0], packet(), NOW, None)["valid_at_collection"]
    assert not validate_quote(INSTRUMENTS[0], packet(), NOW, False)["valid_at_collection"]


def test_five_minute_fallback_uses_bar_timestamp():
    data = packet()
    data["period_minutes"] = 5
    assert validate_quote(INSTRUMENTS[0], data, NOW + timedelta(minutes=1), True)["valid_at_collection"]
    data["period_minutes"] = 15
    assert not validate_quote(INSTRUMENTS[0], data, NOW, True)["valid_at_collection"]


def test_fixture_is_never_usable_live():
    doc = build_snapshot(CALENDAR, {spec.code: packet(spec) for spec in INSTRUMENTS}, NOW, "offline_fixture")
    assert not doc["live_usable_at_collection"]
    assert all(q["quote"] is None and not q["valid_at_collection"] for q in doc["indices"])
    assert not assess_public_snapshot(doc, NOW)["live_usable_at_read"]


def test_reading_old_cached_success_rejected():
    assert not assess_public_snapshot(snapshot(), NOW + timedelta(minutes=6))["live_usable_at_read"]


def test_tampered_positive_flags_do_not_bypass_identity():
    doc = snapshot()
    doc["indices"][0]["source"]["response_name"] = "中证500"
    assert not assess_public_snapshot(doc, NOW)["live_usable_at_read"]


def test_missing_expected_index_rejected():
    doc = snapshot()
    doc["indices"][1] = deepcopy(doc["indices"][0])
    assert not assess_public_snapshot(doc, NOW)["live_usable_at_read"]


def test_schema_valid_for_success_and_failure():
    schema = json.loads((Path(__file__).parents[1] / "schemas/snapshot.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.validate(snapshot())
    validator.validate(build_snapshot({}, {}, NOW))


def test_utc_converts_to_shanghai_without_changing_source_time():
    data = packet(at="2026-10-09T06:30:00Z")
    assert validate_quote(INSTRUMENTS[0], data, NOW, True)["source_timestamp"] == NOW.isoformat()
