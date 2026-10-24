"""Append-only public-market evidence, separate from the formal intraday risk gate."""
from __future__ import annotations

from datetime import datetime, time
import hashlib
import json
from math import isfinite
from pathlib import Path
from urllib.parse import urlsplit

from .core import INSTRUMENTS, TZ, normalized_name, timestamp


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_chain(path):
    path = Path(path)
    rows, previous = [], None
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        body = {k: v for k, v in row.items() if k != "record_hash"}
        if row.get("previous_hash") != previous or row.get("record_hash") != digest(body):
            raise ValueError("evidence_chain_corrupt:" + path.name)
        previous = row["record_hash"]
        rows.append(row)
    return rows


def append_record(path, record_id, payload, recorded_at):
    """Idempotent append; fail closed on edits, truncation or ID reuse."""
    path = Path(path)
    rows = read_chain(path)
    for row in rows:
        if row["record_id"] == record_id:
            if row["payload"] != payload:
                raise ValueError("evidence_id_conflict")
            return row
    body = {"record_id": record_id, "recorded_at": recorded_at, "previous_hash": rows[-1]["record_hash"] if rows else None, "payload": payload}
    row = dict(body, record_hash=digest(body))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        output.flush()
    return row


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or (positive and value <= 0):
        raise ValueError("invalid_market_number")
    return float(value)


def public_url(url):
    parsed = urlsplit(str(url))
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def safe_source(source, as_of):
    """Whitelist public provenance fields; never persist arbitrary input objects."""
    fields = ("source_id", "url", "provider", "interface", "received_at", "source_timestamp", "response_sha256", "http_status", "transport", "identity_verified", "source_code", "source_name", "identity_origin", "secid", "status", "reliability", "methodology", "timestamp_timezone", "timestamp_kind", "volume_unit", "amount_unit", "decision_cutoff", "limitations", "published_at_precision", "excerpt_sha256")
    result = {key: source[key] for key in fields if key in source}
    if not public_url(result.get("url")) or timestamp(result["received_at"]) > as_of:
        raise ValueError("invalid_source_provenance")
    if not result.get("source_id") or not result.get("response_sha256"):
        raise ValueError("missing_source_provenance")
    return result


def validated_daily(spec, item, as_of):
    source = safe_source(item.get("source", {}), as_of)
    if (source.get("identity_verified") is not True or str(source.get("source_code")) != spec.code
            or normalized_name(source.get("source_name", "")) not in {normalized_name(n) for n in spec.names}
            or source.get("identity_origin") != "same_daily_response"):
        raise ValueError("daily_identity_invalid")
    rows, seen = [], {}
    for raw in item.get("daily", []):
        day = datetime.strptime(raw["date"], "%Y-%m-%d").date()
        close_at = datetime.combine(day, time(15), TZ)
        if close_at > as_of or close_at > timestamp(source["received_at"]):
            # A vendor may include today's unfinished daily bar. Exclude it.
            continue
        bar = {"date": day.isoformat(), **{k: number(raw[k], True) for k in ("open", "high", "low", "close")}}
        if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]) or bar["low"] > bar["high"]:
            raise ValueError("daily_ohlc_invalid")
        for key in ("volume", "amount", "pct_change"):
            bar[key] = None if raw.get(key) is None else number(raw[key])
        if any(bar[key] is not None and bar[key] < 0 for key in ("volume", "amount")):
            raise ValueError("daily_volume_invalid")
        bar.update(source_id=source["source_id"], source_timestamp=close_at.isoformat())
        if day in seen and bar != seen[day]:
            raise ValueError("daily_conflicting_date")
        seen[day] = bar
    rows = sorted(seen.values(), key=lambda r: r["date"])
    if not rows:
        raise ValueError("completed_daily_history_unavailable")
    return source, rows


def previous_session(bundle, as_of):
    calendar = bundle.get("calendar", {})
    try:
        days = sorted(set(calendar["dates"]))
        if not days or not days[0] <= as_of.date().isoformat() <= days[-1]:
            return None
        eligible = [d for d in days if d < as_of.date().isoformat()]
        return max(eligible) if eligible else None
    except (KeyError, TypeError, ValueError):
        return None


def auxiliary(raw, kind, expected_day, as_of):
    """A gap is data too. Unsupported or mistimed observations never become facts."""
    gap = {"status": "unavailable", "reason": "verified_public_" + kind + "_not_available", "observations": []}
    if not expected_day:
        return dict(gap, reason="previous_trading_day_unverified")
    accepted = []
    for row in raw.get("observations", []) if isinstance(raw, dict) else []:
        try:
            source = safe_source(row["source"], as_of)
            if row.get("date") != expected_day or timestamp(row["published_at"]) > as_of or row.get("verification") != "source_verified":
                continue
            common = {"date": row["date"], "published_at": row["published_at"], "source": source, "universe": row["universe"], "methodology": row["methodology"]}
            if not common["universe"] or not common["methodology"]:
                continue
            if kind == "etf_flows":
                if row["measurement"] not in ("net_subscription", "share_change_times_nav_estimate") or row["unit"] != "CNY":
                    continue
                common.update(value=number(row["value"]), unit="CNY", measurement=row["measurement"], is_estimate=row["measurement"] == "share_change_times_nav_estimate")
            else:
                counts = {key: row[key] for key in ("advancing", "declining", "unchanged")}
                if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()) or sum(counts.values()) == 0:
                    continue
                common.update(counts)
            accepted.append(common)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    if not accepted:
        return dict(gap, reason=raw.get("reason", gap["reason"]) if isinstance(raw, dict) else gap["reason"])
    # Preserve differing universes/estimates rather than adding incompatible totals.
    return {"status": "available", "date": expected_day, "observations": accepted, "aggregation": "none_different_sources_and_universes_retained"}


def calendar_events(events, as_of):
    official = ("stats.gov.cn", "pbc.gov.cn", "gov.cn", "csrc.gov.cn", "sse.com.cn", "szse.cn", "szse.cn", "cnindex.com.cn", "csindex.com.cn")
    result = []
    for raw in events:
        try:
            source = safe_source(raw["source"], as_of)
            host = urlsplit(source["url"]).hostname or ""
            if not any(host == d or host.endswith("." + d) for d in official) or raw.get("verification") != "source_verified":
                continue
            if timestamp(raw["published_at"]) > as_of:
                continue
            certainty = raw["date_certainty"]
            if certainty not in ("confirmed", "estimated_window", "unscheduled"):
                continue
            event = {k: raw[k] for k in ("event_id", "title", "category", "date_certainty", "published_at", "source_excerpt", "observation", "thesis_link")}
            event["source"] = source
            event["published_at_precision"] = raw.get("published_at_precision", "timestamp")
            event["review_age_days"] = (as_of - timestamp(source["received_at"])).days
            event["needs_source_recheck"] = event["review_age_days"] > 14
            event["date"] = None
            event["scheduled_at"] = None
            event["window"] = None
            if certainty == "confirmed":
                if "T" in raw["date"]:
                    scheduled = timestamp(raw["date"])
                    event["date"] = scheduled.date().isoformat()
                    event["scheduled_at"] = scheduled.isoformat()
                else:
                    datetime.strptime(raw["date"], "%Y-%m-%d")
                    event["date"] = raw["date"]
                if event["date"] < as_of.date().isoformat():
                    continue
            elif certainty == "estimated_window":
                start, end = raw["window"]["start"], raw["window"]["end"]
                datetime.strptime(start, "%Y-%m-%d")
                datetime.strptime(end, "%Y-%m-%d")
                if start > end or end < as_of.date().isoformat():
                    continue
                event["window"] = {"start": start, "end": end}
            result.append(event)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return result


def daily_metrics(rows):
    last = rows[-1]
    prior = rows[-2] if len(rows) >= 2 else None
    metrics = {"date": last["date"], "source_timestamp": last["source_timestamp"], "close": last["close"], "high": last["high"], "low": last["low"], "volume": last["volume"], "amount": last["amount"], "volume_unit": "provider_reported_volume", "amount_unit": "CNY", "intraday_range_pct": (last["high"] / last["low"] - 1) * 100, "return_1d_pct": None, "volume_vs_previous_ratio": None, "return_5_sessions_pct": None, "prior_close": None, "prior_low": None, "prior_high": None}
    if prior:
        metrics.update(return_1d_pct=(last["close"] / prior["close"] - 1) * 100, prior_close=prior["close"], prior_low=prior["low"], prior_high=prior["high"])
        if last["volume"] is not None and prior["volume"]:
            metrics["volume_vs_previous_ratio"] = last["volume"] / prior["volume"]
    if len(rows) >= 6:
        metrics["return_5_sessions_pct"] = (last["close"] / rows[-6]["close"] - 1) * 100
    return metrics


def conditional_scenarios(metrics, relative):
    if not metrics or metrics.get("prior_close") is None:
        observed = {"close_holds_prior_close": None, "low_holds_prior_low": None, "close_breaks_prior_low": None, "growth_not_underperforming": relative is not None and relative >= 0 if relative is not None else None}
    else:
        observed = {"close_holds_prior_close": metrics["close"] >= metrics["prior_close"], "low_holds_prior_low": metrics["low"] >= metrics["prior_low"], "close_breaks_prior_low": metrics["close"] < metrics["prior_low"], "growth_not_underperforming": relative >= 0 if relative is not None else None}
    return {
        "method": "conditional_observation_no_probability_or_price_target",
        "observed_completed_session": metrics.get("date") if metrics else None,
        "checks": observed,
        "cases": [
            {"id": "stabilization", "name": "企稳", "conditions": ["收盘守住前一交易日收盘，日内低点不再下移", "成长相对宽基不再走弱", "后续交易日广度改善并有可核实的ETF净申购支持"], "invalidation": "再度收破前一交易日低点，或反弹时成长与广度持续背离", "next_observation": "核对下个交易日收盘、同口径广度和上一交易日ETF份额变化", "assessment": "conditional_not_confirmed"},
            {"id": "range", "name": "震荡", "conditions": ["前一交易日高低区间内反复，量能和广度信号不一致"], "invalidation": "连续低点下移或有广度支持的向上突破", "next_observation": "比较实际高低点、成交量与同口径涨跌家数", "assessment": "conditional_not_confirmed"},
            {"id": "weakening", "name": "继续走弱", "conditions": ["收盘跌破前一交易日低点，或低点继续下移", "成长相对走弱，且下跌成交量增加或广度恶化"], "invalidation": "收复前一交易日收盘且低点、广度同步改善", "next_observation": "核对收盘与前低的关系、跌日量能和成长相对收益", "assessment": "conditional_not_confirmed"},
        ],
        "sensitivity": [{"driver": "close_vs_prior_close", "observed": metrics.get("close") if metrics else None, "reference": metrics.get("prior_close") if metrics else None, "reference_origin": "verified_completed_daily_bar", "effect": "能否守住前收改变企稳证据方向"}, {"driver": "growth_minus_a500_return_pp", "observed": relative, "reference": 0, "reference_origin": "equality_of_same_session_returns", "effect": "正负号改变成长相对强弱判断；A500只是宽基代理，不是纯大盘指数"}],
    }


def build_evidence(bundle, history_dir, as_of, events=()):
    as_of = timestamp(as_of)
    history_dir = Path(history_dir)
    expected_day = previous_session(bundle, as_of)
    result = {"schema_version": "market_evidence.v1", "generated_at": as_of.isoformat(), "timezone": "Asia/Shanghai", "scope": [s.code for s in INSTRUMENTS], "purpose": "市场证据与趋势观察", "formal_risk_source": "latest.json_only_after_complete_read_time_validation", "formal_risk_substitute": False, "personal_data_collected": False, "previous_trading_day": expected_day, "indices": [], "sources": [], "gaps": [], "methodology": {"thesis": "append_only_confirm_and_disconfirm_evidence_with_falsifiers", "confidence": "qualitative_evidence_coverage_not_forecast_probability", "relative_performance": "399673 daily return minus 000510 daily return, same session; A500 broad-market proxy, not pure large-cap", "close_availability": "Completed bars only; source time is 15:00 Asia/Shanghai, received_at is retrieval time; first_seen_at does not prove historical availability", "causality": "ETF flows cannot identify buyers or prove a market bottom"}}
    usable = {}
    for spec in INSTRUMENTS:
        item = bundle.get("indices", {}).get(spec.code, {})
        index = {"code": spec.code, "name": spec.name, "status": "unavailable", "metrics": None, "supporting_evidence": [], "opposing_evidence": [], "confidence": "insufficient", "thesis_status": "untested", "falsifier": "后续收盘再破前低或低点持续下移，且成长/广度未改善", "next_proof": "下个完成交易日的低点、收盘、量能，以及同口径广度和前一交易日ETF净申购"}
        try:
            source, rows = validated_daily(spec, item, as_of)
            append_record(history_dir / "sources.jsonl", digest(source), source, as_of.isoformat())
            prior_rows = read_chain(history_dir / "daily" / (spec.code + ".jsonl"))
            recorded_ids = {r["record_id"] for r in prior_rows}
            for bar in rows:
                payload = {"code": spec.code, "name": spec.name, "bar": bar, "source": source}
                # Content identities preserve revisions without growing on identical retrievals.
                identity = digest({"code": spec.code, "bar": {k: v for k, v in bar.items() if k != "source_id"}})
                if identity not in recorded_ids:
                    append_record(history_dir / "daily" / (spec.code + ".jsonl"), identity, payload, as_of.isoformat())
                    recorded_ids.add(identity)
            metrics = daily_metrics(rows)
            index.update(status="available", metrics=metrics, source=source, history_records=len(read_chain(history_dir / "daily" / (spec.code + ".jsonl"))), latest_session_is_previous_trading_day=rows[-1]["date"] == expected_day)
            if rows[-1]["date"] != expected_day:
                result["gaps"].append(spec.code + ":daily_not_previous_trading_day")
                index["status"] = "stale_history"
            else:
                usable[spec.code] = metrics
            result["sources"].append(source)
            if metrics["return_1d_pct"] is not None:
                destination = index["supporting_evidence"] if metrics["return_1d_pct"] >= 0 else index["opposing_evidence"]
                destination.append({"fact": "completed_session_close_return", "value_pct": metrics["return_1d_pct"], "date": metrics["date"], "source_id": source["source_id"], "interpretation": "price_observation_not_proof_of_bottom"})
                destination = index["supporting_evidence"] if metrics["low"] >= metrics["prior_low"] else index["opposing_evidence"]
                destination.append({"fact": "low_holds_prior_session_low", "value": metrics["low"] >= metrics["prior_low"], "date": metrics["date"], "source_id": source["source_id"]})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if str(exc).startswith("evidence_"):
                raise
            result["gaps"].append(spec.code + ":completed_daily_evidence_unavailable")
        result["indices"].append(index)
    relative = None
    if len(usable) == 2 and usable["399673"]["date"] == usable["000510"]["date"] and all(m["return_1d_pct"] is not None for m in usable.values()):
        relative = usable["399673"]["return_1d_pct"] - usable["000510"]["return_1d_pct"]
    result["relative_performance"] = {"status": "available" if relative is not None else "unavailable", "growth_minus_broad_market_pp": relative, "growth_code": "399673", "broad_market_proxy_code": "000510", "date": usable.get("399673", {}).get("date") if relative is not None else None}
    result["breadth"] = auxiliary(bundle.get("breadth", {}), "breadth", expected_day, as_of)
    result["etf_flows"] = auxiliary(bundle.get("etf_flows", {}), "etf_flows", expected_day, as_of)
    for key in ("breadth", "etf_flows"):
        if result[key]["status"] == "unavailable":
            result["gaps"].append(key + ":" + result[key]["reason"])
    for index in result["indices"]:
        index["scenarios"] = conditional_scenarios(index["metrics"] if index["status"] == "available" else None, relative)
        if index["status"] == "available":
            index["confidence"] = "limited" if result["gaps"] else "moderate"
            index["thesis_status"] = "watch_evidence_incomplete" if result["gaps"] else "conditional_observation"
        thesis = {k: index[k] for k in ("code", "name", "status", "metrics", "supporting_evidence", "opposing_evidence", "confidence", "thesis_status", "falsifier", "next_proof", "scenarios")}
        thesis.update(relative_performance=result["relative_performance"], breadth=result["breadth"], etf_flows=result["etf_flows"], evidence_as_of=as_of.isoformat(), gaps=list(result["gaps"]))
        append_record(history_dir / "thesis" / (index["code"] + ".jsonl"), digest(thesis), thesis, as_of.isoformat())
    result["catalysts"] = {"events": calendar_events(events, as_of), "unverified_categories": [], "rule": "Confirmed dates require a reviewed official source; windows remain windows; no invented policy dates"}
    for event in result["catalysts"]["events"]:
        append_record(history_dir / "catalysts.jsonl", digest(event), event, as_of.isoformat())
    present = {e["category"] for e in result["catalysts"]["events"]}
    result["catalysts"]["unverified_categories"] = [c for c in ("macro_policy", "index_etf_rebalance", "trading_calendar") if c not in present]
    result["status"] = "available_with_gaps" if usable else "unavailable"
    return result
