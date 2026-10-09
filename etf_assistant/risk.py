"""Transparent intraday observations; no position-specific sell instructions."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from math import isfinite

from .core import INSTRUMENTS, TZ, assess_public_snapshot, identity_matches, timestamp

METHOD = "intraday_close_rules_v1"
RULES = (
    {"id": "peak_drawdown", "metric": "drawdown_from_sampled_peak_pct", "threshold_pct": 1.5, "comparison": ">=", "description": "较当日已观察到的最高分钟收盘点回撤至少1.5%"},
    {"id": "fast_decline", "metric": "return_20m_pct", "threshold_pct": -0.8, "comparison": "<=", "description": "最近20分钟收盘点下跌至少0.8%"},
    {"id": "morning_low_break", "metric": "below_morning_sampled_low_pct", "threshold_pct": 0.3, "comparison": ">=", "description": "跌破上午最低分钟收盘点至少0.3%"},
)


def unavailable(spec, reason):
    return {"canonical_code": spec.code, "canonical_name": spec.name, "status": "unavailable", "level": "unavailable", "as_of": None, "expires_at": None, "metrics": None, "triggers": [], "reasons": [reason]}


def expected_times(day, as_of, period):
    """Regular exchange bars, excluding lunch; opening 09:30 is optional."""
    required = set()
    for start, end in ((time(9, 30), time(11, 30)), (time(13), as_of.time())):
        cursor = datetime.combine(day, start, TZ) + timedelta(minutes=period)
        stop = min(datetime.combine(day, end, TZ), as_of)
        while cursor <= stop:
            required.add(cursor)
            cursor += timedelta(minutes=period)
    return required


def analyze_index(spec, packet, quote):
    if not identity_matches(spec, packet):
        return unavailable(spec, "risk_identity_invalid")
    try:
        as_of = timestamp(quote["source_timestamp"])
        period = packet["period_minutes"]
        if period not in (1, 5) or as_of.second or as_of.microsecond or as_of.minute % period:
            raise ValueError("risk_bar_period_invalid")
        bars = {}
        for row in packet["rows"]:
            at = timestamp(row["time"])
            if at.date() != as_of.date() or at > as_of:
                continue
            if not (time(9, 30) <= at.time() <= time(11, 30) or time(13) <= at.time() <= time(15)):
                raise ValueError("risk_session_time_invalid")
            close = float(row["close"])
            if not isfinite(close) or close <= 0:
                raise ValueError("risk_history_price_invalid")
            if at in bars and bars[at] != close:
                raise ValueError("risk_conflicting_history")
            bars[at] = close
        required = expected_times(as_of.date(), as_of, period)
        if not required or not required.issubset(bars):
            raise ValueError("risk_history_incomplete")
        if bars[as_of] != quote["quote"]["close"]:
            raise ValueError("risk_current_quote_mismatch")
        morning = [close for at, close in bars.items() if at.time() <= time(11, 30)]
        current = bars[as_of]
        reference = as_of - timedelta(minutes=20)
        if reference not in bars or not morning:
            raise ValueError("risk_history_incomplete")
        peak, morning_low = max(bars.values()), min(morning)
        metrics = {
            "drawdown_from_sampled_peak_pct": (1 - current / peak) * 100,
            "return_20m_pct": (current / bars[reference] - 1) * 100,
            "below_morning_sampled_low_pct": (1 - current / morning_low) * 100,
            "current_close": current,
            "sampled_peak_close": peak,
            "morning_sampled_low_close": morning_low,
            "reference_20m_close": bars[reference],
            "reference_20m_timestamp": reference.isoformat(),
            "sample_count": len(bars),
            "bar_period_minutes": period,
        }
        # Do not round before comparing the configured thresholds.
        triggers = [rule["id"] for rule in RULES if (metrics[rule["metric"]] >= rule["threshold_pct"] if rule["comparison"] == ">=" else metrics[rule["metric"]] <= rule["threshold_pct"])]
        level = "elevated" if len(triggers) >= 2 else "watch" if triggers else "no_trigger"
        return {"canonical_code": spec.code, "canonical_name": spec.name, "status": "ok", "level": level, "as_of": as_of.isoformat(), "expires_at": quote["expires_at"], "metrics": metrics, "triggers": triggers, "reasons": []}
    except (KeyError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("risk_") else "risk_history_malformed"
        return unavailable(spec, reason)


def analyze_snapshot(packets, snapshot, now):
    valid = assess_public_snapshot(snapshot, now)["live_usable_at_read"]
    by_code = {quote["canonical_code"]: quote for quote in snapshot["indices"]}
    indices = [analyze_index(spec, packets.get(spec.code, {}), by_code[spec.code]) if valid else unavailable(spec, "fresh_snapshot_required") for spec in INSTRUMENTS]
    complete = all(index["status"] == "ok" for index in indices)
    return {
        "method": METHOD,
        "status": "ok" if complete else "unavailable",
        "scope": "two_indices_intraday_close_observations",
        "backtested": False,
        "sell_order": None,
        "threshold_basis": "configurable_initial_observation_thresholds_not_optimized",
        "rules": [dict(rule) for rule in RULES],
        "indices": indices,
        "consumer_rule": "Use only after the snapshot passes the independent read-time freshness gate; unavailable is not no_trigger; no_trigger does not mean safe.",
    }


def assess_risk_at_read(snapshot, now):
    if not assess_public_snapshot(snapshot, now)["live_usable_at_read"]:
        return {"risk_usable_at_read": False, "risk_reasons": ["fresh_snapshot_required"]}
    try:
        analysis = snapshot["risk_analysis"]
        if analysis["status"] != "ok" or analysis["method"] != METHOD or analysis["sell_order"] is not None:
            raise ValueError
        quotes = {q["canonical_code"]: q for q in snapshot["indices"]}
        indices = {r["canonical_code"]: r for r in analysis["indices"]}
        if len(analysis["indices"]) != 2 or set(indices) != set(quotes):
            raise ValueError
        for code, risk in indices.items():
            quote = quotes[code]
            if risk["status"] != "ok" or risk["as_of"] != quote["source_timestamp"] or risk["expires_at"] != quote["expires_at"] or timestamp(risk["expires_at"]) < timestamp(now):
                raise ValueError
            if risk["metrics"]["current_close"] != quote["quote"]["close"]:
                raise ValueError
        return {"risk_usable_at_read": True, "risk_reasons": []}
    except (KeyError, TypeError, ValueError):
        return {"risk_usable_at_read": False, "risk_reasons": ["risk_analysis_missing_or_invalid"]}
