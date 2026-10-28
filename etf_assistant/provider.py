from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

from .core import TARGET, TZ, identity_matches, shanghai_now, timestamp, validate_quote


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


def completed_minute_packet(packet):
    """Exclude only an evidenced, still-open one-minute bar, keeping raw evidence.

    Eastmoney can label its current one-minute bar with the next minute. The
    original response and hash remain untouched; the selected rows contain only
    bars whose source time is no later than the actual receipt time.
    """
    if type(packet.get("period_minutes")) is not int or packet["period_minutes"] != 1:
        return packet
    try:
        rows = packet["rows"]
        received = timestamp(packet["received_at"])
        times = [timestamp(row["time"]) for row in rows]
        future = [at for at in times if at > received]
        if not future:
            return packet
        expected = received.replace(second=0, microsecond=0) + timedelta(minutes=1)
        # Never drop arbitrary future rows, duplicate bars or reordered history.
        if (len(future) != 1 or not times or times[-1] != expected
                or future[0] != expected or len(times) != len(set(times))
                or times != sorted(times) or expected.date() != received.date()):
            return packet
        raw = packet["raw_response"]["data"]
        trends = raw["trends"]
        values = trends[-1].split(",")
        last = rows[-1]
        if (str(raw["code"]) != str(packet["code"]) or raw["name"] != packet["name"]
                or timestamp(values[0]) != expected or float(values[2]) != float(last["close"])
                or not any(at.date() == received.date() and at <= received for at in times[:-1])):
            return packet
        result = dict(packet)
        result["rows"] = rows[:-1]
        result["excluded_incomplete_bar"] = {
            "timestamp": expected.isoformat(),
            "reason": "next_minute_in_progress_at_receipt",
        }
        return result
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError):
        return packet


class AKShareProvider:
    def __init__(self, deadline=None):
        self.deadline = deadline
        self._calendar = None
        self._valid_packets = {}

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

        def previous_if_still_valid():
            # Reuse only a previously fully validated response, with its original
            # received_at, and recheck its age and risk at the actual new time.
            previous = self._valid_packets.get(spec.code)
            if previous is None or now is None:
                return None
            checked_at = now()
            quote = validate_quote(spec, previous, checked_at, True)
            if not quote["valid_at_collection"]:
                return None
            from .risk import analyze_index
            if analyze_index(spec, previous, quote)["status"] != "ok":
                return None
            result = copy.deepcopy(previous)
            result["attempts"] = list(attempts)
            result["reused_previous_valid_packet"] = True
            return result

        for period in (1, 5):
            for market in spec.markets:
                timeout = 35
                if self.deadline is not None:
                    remaining = (self.deadline - shanghai_now()).total_seconds()
                    if remaining <= 0:
                        cached = previous_if_still_valid()
                        if cached is not None:
                            return cached
                        result = best or {"error": "collection_deadline_exceeded", "received_at": shanghai_now().isoformat()}
                        result["attempts"] = attempts
                        return result
                    timeout = min(timeout, remaining)
                packet = worker_call("minute", "--code", spec.code, "--market", str(market), "--period", str(period), timeout=timeout)
                attempt = {"market": market, "period_minutes": period, "error": packet.get("error"), "error_type": packet.get("error_type"), "response_code": packet.get("code"), "response_name": packet.get("name")}
                attempts.append(attempt)
                if identity_matches(spec, packet) and packet.get("rows"):
                    packet = completed_minute_packet(packet)
                    if packet.get("excluded_incomplete_bar"):
                        attempt["excluded_incomplete_bar"] = packet["excluded_incomplete_bar"]
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
                            self._valid_packets[spec.code] = copy.deepcopy(packet)
                            return packet
                        # A fresh last bar is insufficient for three history-based rules.
                        best = packet
                    if best is None:
                        best = packet
        cached = previous_if_still_valid()
        if cached is not None:
            return cached
        result = best or {"error": "all_minute_sources_unavailable", "received_at": shanghai_now().isoformat()}
        result["attempts"] = attempts
        return result
