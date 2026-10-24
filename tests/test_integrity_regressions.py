"""Regressions for exact risk thresholds and producer/consumer agreement."""
from decimal import Decimal
from datetime import timedelta

import pytest

from etf_assistant.core import INSTRUMENTS, assess_public_snapshot, build_snapshot, validate_quote
from etf_assistant.risk import assess_risk_at_read
from test_risk import CALENDAR, NOW, packets


@pytest.mark.parametrize("period", [1, 5])
@pytest.mark.parametrize("base", ["100", "1234.5"])
@pytest.mark.parametrize("rule,drop", [("peak_drawdown", "1.5"), ("fast_decline", "0.8"), ("morning_low_break", "0.3")])
@pytest.mark.parametrize("price_delta,expected_trigger", [("-0.0000000001", True), ("0", True), ("0.0000000001", False)])
def test_decimal_threshold_below_at_and_above(period, base, rule, drop, price_delta, expected_trigger):
    data = packets(period)
    base = Decimal(base)
    current = base * (1 - Decimal(drop) / 100) + Decimal(price_delta)
    for packet in data.values():
        for row in packet["rows"]:
            row["close"] = float(base)
        packet["rows"][-1]["close"] = float(current)
    document = build_snapshot(CALENDAR, data, NOW)
    assert document["risk_analysis"]["status"] == "ok"
    for index in document["risk_analysis"]["indices"]:
        assert (rule in index["triggers"]) is expected_trigger
    assert assess_risk_at_read(document, NOW)["risk_usable_at_read"]


def test_read_gate_rejects_legacy_missed_exact_twenty_minute_threshold():
    data = packets()
    for packet in data.values():
        packet["rows"][-1]["close"] = 99.2
    document = build_snapshot(CALENDAR, data, NOW)
    index = document["risk_analysis"]["indices"][0]
    assert "fast_decline" in index["triggers"]
    # Binary arithmetic alone yields -0.7999999999999896 for 100 -> 99.2.
    index["triggers"].remove("fast_decline")
    index["level"] = "watch"
    assert not assess_risk_at_read(document, NOW)["risk_usable_at_read"]


@pytest.mark.parametrize("mutation", ["canonical_name", "identity_flag", "receipt_future", "receipt_before_source", "receipt_missing", "age", "age_boolean", "hash_missing", "hash_invalid", "source_provider", "source_interface", "source_timestamp_kind", "source_timezone", "period_boolean"])
def test_read_gate_rejects_inconsistent_source_contract(mutation):
    document = build_snapshot(CALENDAR, packets(), NOW)
    quote = document["indices"][0]
    source = quote["source"]
    if mutation == "canonical_name":
        quote["canonical_name"] = "wrong index"
    elif mutation == "identity_flag":
        quote["identity_verified"] = False
    elif mutation == "receipt_future":
        source["received_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "receipt_before_source":
        source["received_at"] = (NOW - timedelta(seconds=1)).isoformat()
    elif mutation == "receipt_missing":
        source["received_at"] = None
    elif mutation in ("age", "age_boolean"):
        quote["age_seconds"] = 100 if mutation == "age" else False
    elif mutation in ("hash_missing", "hash_invalid"):
        source["response_sha256"] = None if mutation == "hash_missing" else "a" * 63
    elif mutation == "source_provider":
        source["provider"] = "unknown"
    elif mutation == "source_interface":
        source["interface"] = "search"
    elif mutation == "source_timestamp_kind":
        source["timestamp_kind"] = "received_at"
    elif mutation == "source_timezone":
        source["timestamp_timezone"] = "UTC"
    else:
        source["period_minutes"] = True
    assert not assess_public_snapshot(document, NOW)["live_usable_at_read"]
    assert not assess_risk_at_read(document, NOW)["risk_usable_at_read"]


@pytest.mark.parametrize("field,value", [("received_at", None), ("response_sha256", None), ("response_sha256", "bad hash"), ("period_minutes", True)])
def test_producer_requires_receipt_hash_and_integer_period(field, value):
    packet = packets()["000510"]
    packet[field] = value
    assert not validate_quote(INSTRUMENTS[0], packet, NOW, True)["valid_at_collection"]


@pytest.mark.parametrize("mutation", ["risk_name", "count_fraction", "count_infinite", "count_too_large", "period_boolean"])
def test_risk_contract_rejects_wrong_name_or_impossible_sample_count(mutation):
    document = build_snapshot(CALENDAR, packets(), NOW)
    index = document["risk_analysis"]["indices"][0]
    if mutation == "risk_name":
        index["canonical_name"] = "wrong index"
    elif mutation == "period_boolean":
        index["metrics"]["bar_period_minutes"] = True
    else:
        index["metrics"]["sample_count"] = {"count_fraction": 210.5, "count_infinite": float("inf"), "count_too_large": 1000000}[mutation]
    assert not assess_risk_at_read(document, NOW)["risk_usable_at_read"]


@pytest.mark.parametrize("field", ["generated_at", "target_at", "expires_at", "source_timestamp", "received_at", "quote_expiry"])
def test_public_payload_requires_explicit_time_offset(field):
    document = build_snapshot(CALENDAR, packets(), NOW)
    if field in ("generated_at", "target_at", "expires_at"):
        document[field] = document[field][:-6]
    elif field == "received_at":
        source = document["indices"][0]["source"]
        source[field] = source[field][:-6]
    else:
        key = "expires_at" if field == "quote_expiry" else field
        document["indices"][0][key] = document["indices"][0][key][:-6]
    assert not assess_risk_at_read(document, NOW)["risk_usable_at_read"]
