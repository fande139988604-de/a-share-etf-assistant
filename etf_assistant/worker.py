"""One bounded subprocess per AKShare call; raw identity remains attached to bars."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from unittest.mock import patch
from urllib.parse import urlsplit

from .core import INSTRUMENTS, TZ, shanghai_now


def run_worker(command: str, code: str | None = None, market: int | None = None, period: int = 1) -> dict:
    import akshare as ak
    import akshare.index.index_zh_em as index_module
    import requests

    original_get = requests.get
    captured = []
    used_transport = "requests"

    def bounded_get(url, *args, **kwargs):
        nonlocal used_transport
        kwargs.setdefault("timeout", (4, 8))
        try:
            response = original_get(url, *args, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            # Some free-source edges reject the default TLS client. Keep
            # certificate verification enabled and use AKShare's curl dependency.
            from curl_cffi import requests as curl_requests
            browser_kwargs = dict(kwargs, timeout=8, impersonate="chrome")
            response = curl_requests.get(url, *args, **browser_kwargs)
            used_transport = "curl_cffi_chrome"
        response.raise_for_status()
        if urlsplit(str(url)).path in ("/api/qt/stock/trends2/get", "/api/qt/stock/kline/get"):
            captured.append((response.json(), dict(kwargs.get("params", {})), shanghai_now().isoformat()))
        return response

    with patch.object(requests, "get", bounded_get):
        if command == "calendar":
            frame = ak.tool_trade_date_hist_sina()
            return {"source": "akshare.tool_trade_date_hist_sina", "received_at": shanghai_now().isoformat(), "dates": [str(d)[:10] for d in frame["trade_date"]], "akshare_version": ak.__version__}
        spec = next(s for s in INSTRUMENTS if s.code == code)
        if market not in spec.markets or period not in (1, 5):
            raise ValueError("unsupported_market_or_period")
        # Avoid a separate, paginated mapping API. Pin only a known candidate and
        # then inspect the actual code/name in the SAME minute-data response.
        with patch.object(index_module, "index_code_id_map_em", return_value={code: market}):
            frame = ak.index_zh_a_hist_min_em(symbol=code, period=str(period), start_date="1979-01-01 00:00:00", end_date="2222-01-01 00:00:00")
    if not captured:
        raise ValueError("raw_metadata_not_captured")
    raw, params, received_at = captured[-1]
    data = raw.get("data") or {}
    records = data.get("trends" if period == 1 else "klines") or []
    raw_by_time = {str(line).split(",")[0]: str(line).split(",") for line in records}
    rows = []
    for record in frame.to_dict("records"):
        time_text = str(record["时间"])
        close = float(record["收盘"])
        # Provider timestamps may omit seconds; normalize both before comparing.
        from .core import timestamp
        matching = raw_by_time.get(time_text) or raw_by_time.get(time_text[:16])
        if matching is None or timestamp(matching[0]) != timestamp(time_text) or float(matching[2]) != close:
            raise ValueError("akshare_raw_bar_mismatch")
        rows.append({"time": time_text, "close": close})
    return {
        "code": str(data.get("code", "")),
        "name": data.get("name"),
        "secid": params.get("secid"),
        "identity_origin": "same_minute_response",
        "period_minutes": period,
        "received_at": received_at,
        "response_sha256": hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "rows": rows,
        "raw_response": raw,
        "akshare_version": ak.__version__,
        "transport": used_transport,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["calendar", "minute"])
    parser.add_argument("--code")
    parser.add_argument("--market", type=int)
    parser.add_argument("--period", type=int, default=1)
    args = parser.parse_args()
    try:
        with redirect_stdout(io.StringIO()):
            result = run_worker(args.command, args.code, args.market, args.period)
    except Exception as exc:
        # No exception text, URLs, headers, environment, tokens or credentials.
        result = {"error": "source_call_failed", "error_type": type(exc).__name__, "received_at": shanghai_now().isoformat()}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
