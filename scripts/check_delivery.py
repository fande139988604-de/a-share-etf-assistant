"""A successful deployment is not proof of timely, usable delivery."""
import json
from pathlib import Path
import sys


def check(snapshot, reports):
    if not reports:
        raise ValueError("verification_reports_missing")
    for report in reports:
        if not report.get("public_https_verified") or not report.get("published_snapshot_matches"):
            raise ValueError("public_content_not_verified")
        if snapshot["status"] != "market_closed" and not (report.get("live_usable_at_read") and report.get("risk_usable_at_read")):
            raise ValueError("fresh_risk_delivery_not_verified")


if __name__ == "__main__":
    docs = [json.loads(Path(path).read_text(encoding="utf-8")) for path in sys.argv[1:]]
    check(docs[0], docs[1:])
