"""Only publish output produced in this run, never an old checked-out snapshot."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jsonschema import Draft202012Validator
from etf_assistant.cli import write_json
from etf_assistant.core import assess_public_snapshot, shanghai_now, timestamp
from etf_assistant.risk import assess_risk_at_read
from scripts.render_snapshot import render_snapshot


def prepare(source, output, started_at, now=None):
    now = shanghai_now() if now is None else timestamp(now)
    document = json.loads(Path(source).read_text(encoding="utf-8"))
    schema = json.loads((Path(__file__).parents[1] / "schemas/snapshot.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(document)
    if document["data_kind"] != "live" or not timestamp(started_at) <= timestamp(document["generated_at"]) <= now:
        raise ValueError("not_this_run_live_collection")
    if document["status"] == "ok" and not assess_public_snapshot(document, now)["live_usable_at_read"]:
        raise ValueError("success_expired_before_publication")
    if document.get("risk_analysis", {}).get("status") == "ok" and not assess_risk_at_read(document, now)["risk_usable_at_read"]:
        raise ValueError("invalid_risk_before_publication")
    output = Path(output)
    write_json(output / "latest.json", document)
    write_json(output / "archive" / (document["trading_day"]["date"] + ".json"), document)
    (output / "snapshot.html").write_text(render_snapshot(document), encoding="utf-8")
    write_json(output / "verification.json", {"checked_at": now.isoformat(), "snapshot_generated_at": document["generated_at"], "public_https_verified": False, "reason": "publication_not_yet_verified"})
    (output / ".nojekyll").touch()
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", default="public")
    parser.add_argument("--started-at", required=True)
    args = parser.parse_args()
    prepare(args.source, args.output, args.started_at)
