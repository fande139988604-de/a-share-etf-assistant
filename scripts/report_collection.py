"""Print bounded, non-sensitive collection diagnostics from recorded evidence."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from etf_assistant.core import timestamp

ATTEMPT_FIELDS = ("market", "period_minutes", "error", "error_type",
                  "response_code", "response_name", "quote_status",
                  "risk_status", "risk_reasons", "excluded_incomplete_bar")


def summarize(evidence):
    results = []
    for attempt in evidence.get("collection_attempts", []):
        item = {"generated_at": attempt.get("generated_at"),
                "status": attempt.get("status"),
                "risk_status": attempt.get("risk_status"), "indices": {}}
        try:
            generated = timestamp(attempt["generated_at"])
        except (KeyError, TypeError, ValueError, OverflowError):
            generated = None
        for code, packet in attempt.get("packets", {}).items():
            info = {"error": packet.get("error"),
                    "excluded_incomplete_bar": packet.get("excluded_incomplete_bar"),
                    "reused_previous_valid_packet": packet.get("reused_previous_valid_packet", False),
                    "attempts": [{key: trial[key] for key in ATTEMPT_FIELDS if key in trial}
                                 for trial in packet.get("attempts", [])]}
            future = []
            for row in packet.get("rows", []):
                try:
                    value = timestamp(row["time"])
                    if generated is not None and value > generated:
                        future.append(value.isoformat())
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
            if future:
                info["future_bar_count"] = len(future)
                info["first_future_bar"] = min(future)
                info["last_future_bar"] = max(future)
            item["indices"][code] = info
        results.append(item)
    return {"collection_attempts": results}


def main(path):
    try:
        evidence = json.loads(Path(path).read_text(encoding="utf-8"))
        print(json.dumps(summarize(evidence), ensure_ascii=False, allow_nan=False))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"collection_diagnostics_error": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
