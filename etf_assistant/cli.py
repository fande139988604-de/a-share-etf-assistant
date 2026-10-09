from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from .core import INSTRUMENTS, TARGET, TZ, assess_public_snapshot, build_snapshot, calendar_state, shanghai_now, timestamp
from .provider import AKShareProvider


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def collect(provider, clock=shanghai_now) -> tuple[dict, dict]:
    calendar = provider.calendar()
    now = clock()
    trading = calendar_state(calendar, now.date())
    packets = {}
    if trading["is_trading_day"] is True:
        for spec in INSTRUMENTS:
            packets[spec.code] = provider.minutes(spec, now=clock)
    now = clock()  # Network latency counts against freshness.
    return build_snapshot(calendar, packets, now), {"calendar": calendar, "packets": packets}


def collect_command(args) -> int:
    provider = AKShareProvider()
    now = shanghai_now()
    target = datetime.combine(now.date(), TARGET, TZ)
    if args.wait and now < target:
        remaining = (target - now).total_seconds()
        if remaining > 900:
            raise SystemExit("Wait must start within 15 minutes before 14:30 Asia/Shanghai.")
        while shanghai_now() < target:
            time.sleep(min(10, max(0.01, (target - shanghai_now()).total_seconds())))
    snapshot, raw = collect(provider)
    while args.retry_window and snapshot["status"] == "unavailable" and shanghai_now() < target + timedelta(minutes=5):
        time.sleep(10)
        snapshot, raw = collect(provider)
    destination = Path(args.output)
    write_json(destination, snapshot)  # Failure replaces old output as well.
    write_json(destination.parent / "archive" / (snapshot["trading_day"]["date"] + ".json"), snapshot)
    if args.evidence:
        write_json(Path(args.evidence), raw)
    print(json.dumps({"status": snapshot["status"], "generated_at": snapshot["generated_at"], "live_usable_at_collection": snapshot["live_usable_at_collection"], "indices": [{"code": q["canonical_code"], "status": q["status"], "source_timestamp": q["source_timestamp"]} for q in snapshot["indices"]]}, ensure_ascii=False))
    return 0 if snapshot["status"] in ("ok", "market_closed") else 2


def live_test(args) -> int:
    provider = AKShareProvider()
    calendar = provider.calendar()
    packets = {}
    for spec in INSTRUMENTS:
        packets[spec.code] = provider.minutes(spec)
    now = shanghai_now()
    snapshot = build_snapshot(calendar, packets, now)
    report = {
        "test": "real_network_akshare_probe",
        "tested_at": now.isoformat(),
        "calendar": calendar_state(calendar, now.date()),
        "indices": [],
        "fresh_1430_snapshot_verified": snapshot["live_usable_at_collection"],
        "deployment_verified": False,
        "chatgpt_action_verified": False,
    }
    from .core import identity_matches
    for spec in INSTRUMENTS:
        packet = packets[spec.code]
        rows = packet.get("rows", [])
        times = sorted(row["time"] for row in rows)
        near_target = [r for r in rows if str(r["time"]).startswith(now.date().isoformat()) and str(r["time"])[11:16] == "14:30"]
        report["indices"].append({"code": spec.code, "identity_verified": identity_matches(spec, packet), "minute_rows": len(rows), "first_source_timestamp": times[0] if times else None, "latest_source_timestamp": times[-1] if times else None, "today_1430_rows": near_target, "attempts": packet.get("attempts", []), "error": packet.get("error"), "akshare_version": packet.get("akshare_version"), "transport": packet.get("transport")})
    directory = Path(args.output_dir)
    write_json(directory / "report.json", report)
    write_json(directory / "raw-evidence.json", {"calendar": calendar, "packets": packets})
    write_json(directory / "snapshot.json", snapshot)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(row["identity_verified"] and row["minute_rows"] for row in report["indices"]) and report["calendar"]["is_trading_day"] is not None else 2


def verify_url(args) -> int:
    import requests
    from jsonschema import Draft202012Validator

    report = {"checked_at": shanghai_now().isoformat(), "public_https_verified": False, "schema_verified": False, "chatgpt_action_verified": False, "live_usable_at_read": False, "reasons": []}
    if urlsplit(args.url).scheme != "https" or urlsplit(args.url).username or urlsplit(args.url).password:
        report["reasons"] = ["public_https_url_required"]
    else:
        try:
            # No authentication, cookies, credentials, proxy credentials or env dumps.
            expected = None
            params = None
            if args.expected:
                expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
                params = {"v": hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
            response = requests.get(args.url, params=params, timeout=(5, 20), headers={"Cache-Control": "no-cache"}, allow_redirects=False)
            report["http_status"] = response.status_code
            report["content_type"] = response.headers.get("Content-Type", "")
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("unexpected_http_status")
            document = response.json()
            schema = json.loads((Path(__file__).parents[1] / "schemas" / "snapshot.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(document)
            if expected is not None:
                report["published_snapshot_matches"] = document == expected
                if document != expected:
                    raise ValueError("published_snapshot_mismatch")
            report.update(public_https_verified=True, schema_verified=True)
            report.update(assess_public_snapshot(document, shanghai_now()))
            from .risk import assess_risk_at_read
            report.update(assess_risk_at_read(document, shanghai_now()))
        except Exception as exc:
            report["reasons"].append("https_or_schema_check_failed:" + type(exc).__name__)
    write_json(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["public_https_verified"] else 2


def read_local(args) -> int:
    try:
        document = json.loads(Path(args.path).read_text(encoding="utf-8"))
        result = assess_public_snapshot(document, shanghai_now())
        from .risk import assess_risk_at_read
        result.update(assess_risk_at_read(document, shanghai_now()))
        result["snapshot"] = document
    except (OSError, ValueError):
        result = {"live_usable_at_read": False, "reasons": ["local_snapshot_unavailable"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["live_usable_at_read"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="14:30 A-share index snapshots with strict source validation")
    sub = parser.add_subparsers(dest="command", required=True)
    collector = sub.add_parser("collect")
    collector.add_argument("--output", default="public/latest.json")
    collector.add_argument("--evidence")
    collector.add_argument("--wait", action="store_true")
    collector.add_argument("--retry-window", action="store_true")
    collector.set_defaults(run=collect_command)
    probe = sub.add_parser("live-test")
    probe.add_argument("--output-dir", default="reports/live")
    probe.set_defaults(run=live_test)
    url = sub.add_parser("verify-url")
    url.add_argument("url")
    url.add_argument("--output", default="reports/public-url.json")
    url.add_argument("--expected", help="Compare HTTPS content with this locally published snapshot")
    url.set_defaults(run=verify_url)
    local = sub.add_parser("read-local")
    local.add_argument("path", nargs="?", default="public/latest.json")
    local.set_defaults(run=read_local)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
