"""Inspect the reminder using original local inputs, with read-time verification."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etf_assistant.core import shanghai_now, timestamp
from etf_assistant.cli import write_json
from etf_assistant.reminder import compose_reminder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default="public/latest.json")
    parser.add_argument("--research", default="public/market-evidence.json")
    parser.add_argument("--output", default="reports/reminder-preview.json")
    parser.add_argument("--as-of", help="Offline verification cutoff only; never changes published source timestamps")
    args = parser.parse_args()
    documents = []
    for path in (args.snapshot, args.research):
        try:
            documents.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            documents.append({})
    result = compose_reminder(*documents, timestamp(args.as_of) if args.as_of else shanghai_now())
    write_json(Path(args.output), result)
    print(json.dumps({"formal_status": result["formal_intraday_risk"]["status"], "web_fallback_required": result["web_fallback_required"], "research_status": result["market_evidence_and_trend"]["status"]}))


if __name__ == "__main__":
    main()
