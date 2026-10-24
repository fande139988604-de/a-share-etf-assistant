"""Collect source evidence without changing the formal risk publication."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_assistant.core import timestamp
from etf_assistant.research_sources import collect_sources, load_curated_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/market-input.json")
    parser.add_argument("--max-seconds", type=float, default=45)
    parser.add_argument("--as-of", help="Decision cutoff with timezone; defaults to actual collection start.")
    parser.add_argument("--evidence", help="Optional reviewed public research JSON; personal investment fields are rejected.")
    args = parser.parse_args()
    bundle = collect_sources(timestamp(args.as_of) if args.as_of else None, args.max_seconds)
    if args.evidence:
        bundle["curated_evidence"] = load_curated_evidence(args.evidence)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "indices": {code: {key: entry["source"].get(key) for key in ("status", "reason", "http_status", "source_code", "source_name", "source_timestamp", "attempts")} for code, entry in bundle["indices"].items()},
                      "calendar": bundle["calendar"].get("status", "unavailable")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
