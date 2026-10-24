"""Materialize reviewed public evidence without changing formal snapshot results."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etf_assistant.cli import write_json
from etf_assistant.core import shanghai_now
from etf_assistant.evidence import build_evidence
from scripts.render_market_evidence import render_market_evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="reports/market-input.json")
    parser.add_argument("--output", default="public")
    parser.add_argument("--events", default="config/catalysts.json")
    args = parser.parse_args()
    try:
        bundle = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        bundle = {"indices": {}, "breadth": {}, "etf_flows": {}}
    events = json.loads(Path(args.events).read_text(encoding="utf-8"))["events"] if Path(args.events).exists() else []
    curated = bundle.get("curated_evidence", {})
    for category in ("breadth", "etf_flows"):
        if curated.get(category):
            bundle[category] = curated[category]
    events = [*events, *curated.get("catalysts", [])]
    output = Path(args.output)
    document = build_evidence(bundle, output / "research", shanghai_now(), events)
    write_json(output / "market-evidence.json", document)
    (output / "market-evidence.html").write_text(render_market_evidence(document), encoding="utf-8")
    print(json.dumps({"status": document["status"], "generated_at": document["generated_at"], "gaps": document["gaps"], "event_count": len(document["catalysts"]["events"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
