from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
import unicodedata
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
MAX_AGE = 300
TARGET = time(14, 30)


def shanghai_now() -> datetime:
    return datetime.now(TZ)


def timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Eastmoney's minute times are explicitly defined as exchange local time.
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def normalized_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value)).upper().split())


@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    names: tuple[str, ...]
    markets: tuple[int, ...]


INSTRUMENTS = (
    Instrument("000510", "中证A500", ("中证A500", "中证A500指数"), (1, 2)),
    Instrument("399673", "创业板50", ("创业板50", "创业板50指数"), (0,)),
)


def identity_matches(spec: Instrument, packet: dict) -> bool:
    return (
        str(packet.get("code", "")) == spec.code
        and normalized_name(packet.get("name", "")) in {normalized_name(n) for n in spec.names}
        and str(packet.get("secid", "")) in {f"{m}.{spec.code}" for m in spec.markets}
        and packet.get("identity_origin") == "same_minute_response"
    )


def calendar_state(calendar: dict, day: date) -> dict:
    result = {
        "source": calendar.get("source", "akshare.tool_trade_date_hist_sina"),
        "checked_at": calendar.get("received_at"),
        "date": day.isoformat(),
        "is_trading_day": None,
        "coverage_start": None,
        "coverage_end": None,
        "status": "unknown",
    }
    try:
        days = sorted({date.fromisoformat(d) for d in calendar["dates"]})
        if not days:
            return result
        result.update(coverage_start=days[0].isoformat(), coverage_end=days[-1].isoformat())
        if not days[0] <= day <= days[-1]:
            result["reason"] = "calendar_out_of_coverage"
            return result
        # Even government make-up workdays on weekends remain exchange holidays.
        result.update(is_trading_day=day in days and day.weekday() < 5, status="verified")
    except (KeyError, TypeError, ValueError):
        result["reason"] = "calendar_unavailable_or_malformed"
    return result


def validate_quote(spec: Instrument, packet: dict, now: datetime, trade_day: bool | None) -> dict:
    now = timestamp(now)
    target = datetime.combine(now.date(), TARGET, TZ)
    result = {
        "canonical_code": spec.code,
        "canonical_name": spec.name,
        "instrument_type": "index",
        "status": "unavailable",
        "valid_at_collection": False,
        "identity_verified": False,
        "source": {
            "provider": "eastmoney",
            "interface": "akshare.index_zh_a_hist_min_em",
            "secid": packet.get("secid"),
            "response_code": packet.get("code"),
            "response_name": packet.get("name"),
            "identity_origin": packet.get("identity_origin"),
            "response_sha256": packet.get("response_sha256"),
            "received_at": packet.get("received_at"),
            "period_minutes": packet.get("period_minutes"),
            "transport": packet.get("transport"),
            "timestamp_kind": "source_minute_bar_time",
            "timestamp_timezone": "Asia/Shanghai",
        },
        "source_timestamp": None,
        "age_seconds": None,
        "expires_at": None,
        "quote": None,
        "reasons": [],
    }

    def reject(reason: str) -> dict:
        result["reasons"].append(reason)
        result["status"] = reason
        return result

    if trade_day is None:
        return reject("calendar_unverified")
    if trade_day is False:
        return reject("market_closed")
    if packet.get("error"):
        return reject("source_unavailable")
    if not identity_matches(spec, packet):
        return reject("identity_mismatch_or_missing")
    result["identity_verified"] = True
    if packet.get("period_minutes") not in (1, 5):
        return reject("unsupported_bar_period")
    if now < target:
        return reject("before_target_window")
    rows = packet.get("rows", [])
    if not rows:
        return reject("no_minute_data")
    try:
        parsed_rows = [(timestamp(row["time"]), row) for row in rows]
    except (KeyError, TypeError, ValueError):
        return reject("timestamp_missing_or_invalid")
    today = [(t, row) for t, row in parsed_rows if t.date() == now.date()]
    if not today:
        return reject("wrong_trading_date")
    if any(t > now for t, _ in today):
        return reject("future_source_timestamp")
    eligible = [(t, row) for t, row in today if t <= target + timedelta(seconds=MAX_AGE)]
    if not eligible:
        return reject("outside_target_window")
    source_time, row = max(eligible, key=lambda pair: pair[0])
    age = (now - source_time).total_seconds()
    result.update(source_timestamp=source_time.isoformat(), age_seconds=age)
    if source_time < target - timedelta(seconds=MAX_AGE):
        return reject("outside_target_window")
    if source_time.time() < time(13, 0):
        return reject("not_afternoon_data")
    if age > MAX_AGE:
        return reject("stale_data")
    if now > target + timedelta(seconds=MAX_AGE):
        return reject("missed_target_window")
    try:
        close = float(row["close"])
        if not isfinite(close) or close <= 0:
            return reject("invalid_price")
    except (KeyError, TypeError, ValueError):
        return reject("invalid_price")
    # Reject conflicting duplicate bars instead of silently choosing one.
    same_time = [r for t, r in today if t == source_time]
    if any(r != row for r in same_time):
        return reject("conflicting_duplicate_timestamp")
    result.update(
        status="ok",
        valid_at_collection=True,
        expires_at=min(source_time + timedelta(seconds=MAX_AGE), target + timedelta(seconds=MAX_AGE)).isoformat(),
        quote={"close": close, "unit": "index_points"},
    )
    return result


def build_snapshot(calendar: dict, packets: dict[str, dict], now: datetime, data_kind: str = "live") -> dict:
    now = timestamp(now)
    trading = calendar_state(calendar, now.date())
    quotes = [validate_quote(spec, packets.get(spec.code, {"error": "missing"}), now, trading["is_trading_day"]) for spec in INSTRUMENTS]
    valid = all(q["valid_at_collection"] for q in quotes) and data_kind == "live"
    if data_kind != "live":
        for quote in quotes:
            quote["valid_at_collection"] = False
            quote["quote"] = None
            quote["status"] = "offline_fixture"
            quote["reasons"].append("offline_fixture_not_live")
    status = "ok" if valid else "unavailable"
    if trading["is_trading_day"] is False:
        status = "market_closed"
    elif trading["is_trading_day"] is None:
        status = "calendar_unverified"
    return {
        "schema_version": "1.0",
        "data_kind": data_kind,
        "status": status,
        "generated_at": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "target_at": datetime.combine(now.date(), TARGET, TZ).isoformat(),
        "max_age_seconds": MAX_AGE,
        "live_usable_at_collection": valid,
        "expires_at": min(q["expires_at"] for q in quotes) if valid else None,
        "trading_day": trading,
        "indices": quotes,
        "consumer_rule": "Recheck date, identity, source_timestamp, expires_at and age<=300 seconds at READ time. Collection validity does not imply current freshness.",
        "signal": None,
    }


def assess_public_snapshot(document: dict, now: datetime) -> dict:
    """Independent read-time gate; never trust the producer's true flags alone."""
    now = timestamp(now)
    reasons = []
    if document.get("data_kind") != "live" or document.get("status") != "ok":
        reasons.append("snapshot_not_live_and_valid")
        for quote in document.get("indices", []):
            reasons.extend(str(reason) for reason in quote.get("reasons", []))
        return {"checked_at": now.isoformat(), "live_usable_at_read": False, "reasons": sorted(set(reasons))}
    try:
        generated = timestamp(document["generated_at"])
        target = timestamp(document["target_at"])
        if generated > now or generated.date() != now.date():
            reasons.append("generated_date_or_time_invalid")
        if target != datetime.combine(now.date(), TARGET, TZ):
            reasons.append("target_invalid")
        if not target <= now <= target + timedelta(seconds=MAX_AGE):
            reasons.append("outside_read_window")
        if timestamp(document["expires_at"]) < now:
            reasons.append("snapshot_expired")
        trade = document["trading_day"]
        if trade["is_trading_day"] is not True or trade["status"] != "verified" or trade["date"] != now.date().isoformat():
            reasons.append("trading_day_not_verified")
        quotes = document["indices"]
        if len(quotes) != len(INSTRUMENTS):
            reasons.append("unexpected_index_count")
        by_code = {q["canonical_code"]: q for q in quotes}
        for spec in INSTRUMENTS:
            quote = by_code[spec.code]
            source = quote["source"]
            packet = {"code": source["response_code"], "name": source["response_name"], "secid": source["secid"], "identity_origin": source["identity_origin"]}
            if not identity_matches(spec, packet) or quote["instrument_type"] != "index":
                reasons.append(f"identity_invalid:{spec.code}")
            ts = timestamp(quote["source_timestamp"])
            age = (now - ts).total_seconds()
            if not 0 <= age <= MAX_AGE or ts.date() != now.date() or abs((ts - target).total_seconds()) > MAX_AGE:
                reasons.append(f"stale_or_invalid_timestamp:{spec.code}")
            if quote["status"] != "ok" or quote["valid_at_collection"] is not True:
                reasons.append(f"collection_invalid:{spec.code}")
            close = float(quote["quote"]["close"])
            if close <= 0 or not isfinite(close):
                reasons.append(f"invalid_price:{spec.code}")
    except (KeyError, TypeError, ValueError):
        reasons.append("malformed_snapshot")
    return {"checked_at": now.isoformat(), "live_usable_at_read": not reasons, "reasons": sorted(set(reasons))}
