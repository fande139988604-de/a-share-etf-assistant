"""Public, bounded research collection; never used to calculate formal risk."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

from .core import INSTRUMENTS, TZ, normalized_name, shanghai_now, timestamp

DAILY_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
MAX_RESPONSE_BYTES = 2_000_000


def daily_url(code: str, market: int, as_of: datetime) -> str:
    spec = next((item for item in INSTRUMENTS if item.code == code), None)
    if spec is None or market not in spec.markets:
        raise ValueError("unsupported_index_or_market")
    local = timestamp(as_of)
    return DAILY_ENDPOINT + "?" + urlencode({
        "secid": f"{market}.{code}", "klt": "101", "fqt": "0",
        "beg": (local.date() - timedelta(days=200)).strftime("%Y%m%d"),
        "end": local.strftime("%Y%m%d"), "lmt": "120",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    })


def parse_daily_response(raw: bytes, *, code: str, market: int, url: str,
                         received_at: str, as_of: datetime, http_status: int = 200,
                         transport: str = "requests") -> dict:
    """Verify identity from the same body, then retain only completed bars."""
    spec = next((item for item in INSTRUMENTS if item.code == code), None)
    if spec is None or market not in spec.markets:
        raise ValueError("unsupported_index_or_market")
    cutoff = timestamp(as_of)
    response_hash = hashlib.sha256(raw).hexdigest()
    source = {
        "source_id": f"eastmoney.daily.{code}.{response_hash[:20]}",
        "provider": "eastmoney", "url": url, "http_status": http_status,
        "received_at": timestamp(received_at).isoformat(), "source_timestamp": None,
        "response_sha256": response_hash, "identity_verified": False,
        "identity_origin": "same_daily_response", "source_code": None,
        "source_name": None, "secid": f"{market}.{code}", "status": "unavailable",
        "transport": transport, "timestamp_timezone": "Asia/Shanghai",
        "timestamp_kind": "completed_daily_bar_exchange_close",
        "volume_unit": "provider_reported_volume", "amount_unit": "CNY",
        "adjustment": "unadjusted", "decision_cutoff": cutoff.isoformat(),
        "limitations": ["Volume is retained in the provider's original convention; no unverified conversion to shares or ETF subscriptions is applied.",
                        "A completed daily bar does not establish intraday market breadth or formal 14:30 risk."],
    }
    result = {"daily": [], "source": source}

    def fail(reason: str) -> dict:
        source["reason"] = reason
        return result

    if http_status != 200:
        return fail("http_status_not_200")
    try:
        if len(raw) > MAX_RESPONSE_BYTES:
            return fail("response_too_large")
        payload = json.loads(raw)
        data = payload.get("data") or {}
        source.update(source_code=str(data.get("code", "")), source_name=data.get("name"))
        if payload.get("rc") != 0:
            return fail("provider_error_response")
        if (source["source_code"] != code or data.get("market") != market
                or normalized_name(source["source_name"]) not in {normalized_name(n) for n in spec.names}):
            return fail("daily_identity_mismatch")
        source["identity_verified"] = True
        klines = data.get("klines")
        if not isinstance(klines, list) or not klines:
            return fail("daily_history_empty")
        rows, seen, excluded = [], set(), 0
        for line in klines:
            values = str(line).split(",")
            if len(values) < 9:
                return fail("daily_row_malformed")
            day = date.fromisoformat(values[0])
            if day.isoformat() != values[0] or day in seen or day.weekday() >= 5:
                return fail("daily_date_invalid_or_duplicate")
            seen.add(day)
            if day > cutoff.date():
                return fail("future_daily_date")
            # A 14:33 reminder cannot use today's eventual close or final volume.
            close_time = datetime.combine(day, time(15, 0), TZ)
            if close_time > cutoff:
                excluded += 1
                continue
            opening, close, high, low, volume, amount = map(float, values[1:7])
            pct_change = float(values[8])
            numbers = (opening, close, high, low, volume, amount, pct_change)
            if (not all(isfinite(value) for value in numbers) or min(opening, close, high, low) <= 0
                    or volume < 0 or amount < 0 or not low <= min(opening, close) <= max(opening, close) <= high):
                return fail("daily_ohlcv_invalid")
            rows.append({
                "date": day.isoformat(), "open": opening, "high": high, "low": low,
                "close": close, "volume": volume, "amount": amount, "pct_change": pct_change,
                "source_id": source["source_id"], "source_timestamp": close_time.isoformat(),
            })
        if not rows:
            return fail("no_completed_daily_bars_at_cutoff")
        rows.sort(key=lambda row: row["date"])
        source.update(status="ok", source_timestamp=rows[-1]["source_timestamp"],
                      excluded_incomplete_bars=excluded, latest_completed_date=rows[-1]["date"])
        result["daily"] = rows
        return result
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return fail("daily_response_invalid")


def fetch_daily(code: str, as_of: datetime) -> dict:
    """Called only inside a disposable subprocess with an outer hard timeout."""
    import requests
    spec = next((item for item in INSTRUMENTS if item.code == code), None)
    if spec is None:
        raise ValueError("unsupported_index")
    attempts = []
    last = None
    for market in spec.markets:
        url = daily_url(code, market, as_of)
        for transport in ("requests", "curl_cffi_chrome"):
            try:
                if transport == "requests":
                    response = requests.get(url, timeout=(3, 5), headers={"Accept": "application/json"})
                else:
                    from curl_cffi import requests as curl_requests
                    response = curl_requests.get(url, timeout=6, impersonate="chrome", headers={"Accept": "application/json"})
                last = parse_daily_response(response.content, code=code, market=market, url=url,
                                            received_at=shanghai_now().isoformat(), as_of=as_of,
                                            http_status=response.status_code, transport=transport)
                attempts.append({"secid": f"{market}.{code}", "transport": transport,
                                 "http_status": response.status_code, "reason": last["source"].get("reason")})
                if last["source"]["status"] == "ok":
                    last["source"]["attempts"] = attempts
                    return last
            except Exception as exc:
                attempts.append({"secid": f"{market}.{code}", "transport": transport, "error_type": type(exc).__name__})
    if last is None:
        last = {"daily": [], "source": {"source_id": f"eastmoney.daily.{code}", "provider": "eastmoney",
                "status": "unavailable", "reason": "daily_transport_failed", "identity_verified": False,
                "received_at": shanghai_now().isoformat(), "source_timestamp": None,
                "url": daily_url(code, spec.markets[0], as_of), "http_status": None, "response_sha256": None}}
    last["source"]["attempts"] = attempts
    return last


def _bounded_worker(command: str, as_of: datetime, max_seconds: float) -> dict:
    arguments = [sys.executable, "-m", "etf_assistant.worker", "calendar"] if command == "calendar" else [
        sys.executable, "-m", "etf_assistant.research_sources", "--worker-code", command, "--as-of", as_of.isoformat()]
    try:
        process = subprocess.run(arguments, capture_output=True, encoding="utf-8", timeout=max_seconds,
                                 env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
        if process.returncode != 0:
            raise ValueError("worker_failed")
        packet = json.loads(process.stdout)
        if not isinstance(packet, dict):
            raise ValueError("worker_invalid_response")
        return packet
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        reason = "research_collection_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "research_worker_failed"
        if command == "calendar":
            return {"status": "unavailable", "dates": [], "reason": reason, "received_at": shanghai_now().isoformat()}
        return {"daily": [], "source": {"source_id": f"eastmoney.daily.{command}", "status": "unavailable",
                "reason": reason, "identity_verified": False, "received_at": shanghai_now().isoformat()}}


def collect_sources(as_of: datetime | None = None, max_seconds: float = 45) -> dict:
    if not 1 <= max_seconds <= 50:
        raise ValueError("max_seconds_must_be_between_1_and_50")
    cutoff = timestamp(as_of or shanghai_now())
    # Independent hard-killed subprocesses bound DNS stalls and third-party code.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {code: executor.submit(_bounded_worker, code, cutoff, max_seconds) for code in ("000510", "399673", "calendar")}
        collected = {code: future.result() for code, future in futures.items()}
    calendar = collected.pop("calendar")
    if calendar.get("error"):
        calendar.update(status="unavailable", reason=calendar["error"], dates=[])
    elif calendar.get("dates"):
        calendar["status"] = "ok"
    return {
        "generated_at": shanghai_now().isoformat(), "as_of": cutoff.isoformat(),
        "indices": collected, "calendar": calendar,
        "breadth": {"status": "unavailable", "reason": "No validated full-market breadth source with defined universe was available.", "observations": []},
        "etf_flows": {"status": "unavailable", "reason": "No verified previous-trading-day broad ETF net-subscription source; turnover and capital-flow estimates are not subscriptions.", "observations": []},
        "catalysts": [],
        "limitations": ["Public data is collected independently from the formal minute-risk snapshot.",
                        "A successful collection today cannot prove historical publication availability at a past decision cutoff."],
    }


def load_curated_evidence(path: str | Path) -> dict:
    """Load only public research fields. The ledger independently validates claims."""
    content = Path(path).read_bytes()
    if len(content) > 1_000_000:
        raise ValueError("curated_evidence_too_large")
    packet = json.loads(content)
    if not isinstance(packet, dict) or set(packet) - {"breadth", "etf_flows", "catalysts", "sources", "limitations"}:
        raise ValueError("unsupported_curated_fields")
    forbidden = {"holdings", "positions", "cost", "cost_basis", "account", "account_id", "account_number",
                 "portfolio", "password", "token", "持仓", "成本", "账户"}

    def inspect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden:
                    raise ValueError("personal_investment_fields_forbidden")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(packet)
    return packet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-code", required=True, choices=[spec.code for spec in INSTRUMENTS])
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    print(json.dumps(fetch_daily(args.worker_code, timestamp(args.as_of)), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
