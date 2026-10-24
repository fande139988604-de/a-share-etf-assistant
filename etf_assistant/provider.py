from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

from .core import TARGET, TZ, identity_matches, shanghai_now, validate_quote


def worker_call(*arguments: str, timeout: int = 35) -> dict:
    try:
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        process = subprocess.run([sys.executable, "-m", "etf_assistant.worker", *arguments], capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=environment)
        if process.returncode != 0:
            return {"error": "worker_failed", "received_at": shanghai_now().isoformat()}
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "worker_timeout", "received_at": shanghai_now().isoformat()}
    except (ValueError, OSError):
        return {"error": "worker_response_invalid", "received_at": shanghai_now().isoformat()}


class AKShareProvider:
    def __init__(self, deadline=None):
        self.deadline = deadline
        self._calendar = None

    def calendar(self) -> dict:
        if self._calendar and self._calendar.get("received_at", "")[:10] == shanghai_now().date().isoformat():
            return self._calendar
        result = worker_call("calendar")
        if result.get("error"):
            result = worker_call("calendar")
        if not result.get("error"):
            self._calendar = result
        return result

    def minutes(self, spec, now=None) -> dict:
        attempts = []
        best = None
        for period in (1, 5):
            for market in spec.markets:
                timeout = 35
                if self.deadline is not None:
                    remaining = (self.deadline - shanghai_now()).total_seconds()
                    if remaining <= 0:
                        result = best or {"error": "collection_deadline_exceeded", "received_at": shanghai_now().isoformat()}
                        result["attempts"] = attempts
                        return result
                    timeout = min(timeout, remaining)
                packet = worker_call("minute", "--code", spec.code, "--market", str(market), "--period", str(period), timeout=timeout)
                attempt = {"market": market, "period_minutes": period, "error": packet.get("error"), "error_type": packet.get("error_type"), "response_code": packet.get("code"), "response_name": packet.get("name")}
                attempts.append(attempt)
                if identity_matches(spec, packet) and packet.get("rows"):
                    packet["attempts"] = list(attempts)
                    if now is None:
                        return packet
                    # Stale minute data may have a usable five-minute fallback.
                    checked_at = now()
                    target = datetime.combine(checked_at.date(), TARGET, TZ)
                    if not target <= checked_at <= target + timedelta(minutes=5):
                        return packet
                    quote = validate_quote(spec, packet, checked_at, True)
                    attempt["quote_status"] = quote["status"]
                    if quote["valid_at_collection"]:
                        from .risk import analyze_index
                        risk = analyze_index(spec, packet, quote)
                        attempt["risk_status"] = risk["status"]
                        attempt["risk_reasons"] = risk["reasons"]
                        if risk["status"] == "ok":
                            return packet
                        # A fresh last bar is insufficient for three history-based rules.
                        best = packet
                    if best is None:
                        best = packet
        result = best or {"error": "all_minute_sources_unavailable", "received_at": shanghai_now().isoformat()}
        result["attempts"] = attempts
        return result
