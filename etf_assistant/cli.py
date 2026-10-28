from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from .core import INSTRUMENTS, TARGET, TZ, assess_public_snapshot, build_snapshot, calendar_state, shanghai_now, timestamp
from .provider import AKShareProvider


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def collect(provider, clock=shanghai_now, calendar=None) -> tuple[dict, dict]:
    calendar = provider.calendar() if calendar is None else calendar
    now = clock()
    trading = calendar_state(calendar, now.date())
    packets = {}
    if trading["is_trading_day"] is True:
        # Workers are isolated processes; the AKShare request patches never race.
        with ThreadPoolExecutor(max_workers=len(INSTRUMENTS)) as executor:
            futures = {spec.code: executor.submit(provider.minutes, spec, now=clock) for spec in INSTRUMENTS}
            packets = {code: future.result() for code, future in futures.items()}
    now = clock()  # Network latency counts against freshness.
    return build_snapshot(calendar, packets, now), {"calendar": calendar, "packets": packets}


def collect_command(args) -> int:
    provider = AKShareProvider()
    now = shanghai_now()
    target = datetime.combine(now.date(), TARGET, TZ)
    started_at = now
    # Fetch/cache the calendar before waiting, not in the publication window.
    calendar = provider.calendar()
    trading = calendar_state(calendar, now.date())
    if args.wait and trading["is_trading_day"] is not False and now < target:
        remaining = (target - now).total_seconds()
        if remaining > 90 * 60:
            raise SystemExit("Wait must start within 90 minutes before 14:30 Asia/Shanghai.")
        while shanghai_now() < target:
            time.sleep(min(10, max(0.01, (target - shanghai_now()).total_seconds())))
    provider.deadline = target + timedelta(seconds=args.capture_seconds)
    snapshot, raw = collect(provider, calendar=calendar)
    attempts = [{"generated_at": snapshot["generated_at"], "status": snapshot["status"], "risk_status": snapshot["risk_analysis"]["status"], "packets": raw["packets"]}]
    while args.retry_window and (snapshot["status"] in ("unavailable", "calendar_unverified") or (snapshot["status"] == "ok" and snapshot["risk_analysis"]["status"] != "ok")) and shanghai_now() + timedelta(seconds=10) < provider.deadline:
        time.sleep(10)
        snapshot, raw = collect(provider)
        attempts.append({"generated_at": snapshot["generated_at"], "status": snapshot["status"], "risk_status": snapshot["risk_analysis"]["status"], "packets": raw["packets"]})
    snapshot["collection"] = {"started_at": started_at.isoformat(), "capture_deadline": provider.deadline.isoformat(), "attempt_count": len(attempts), "schedule_late": started_at > target + timedelta(minutes=5)}
    raw["collection_attempts"] = attempts
    destination = Path(args.output)
    write_json(destination, snapshot)  # Failure replaces old output as well.
    write_json(destination.parent / "archive" / (snapshot["trading_day"]["date"] + ".json"), snapshot)
    if args.evidence:
        write_json(Path(args.evidence), raw)
    print(json.dumps({"status": snapshot["status"], "generated_at": snapshot["generated_at"], "live_usable_at_collection": snapshot["live_usable_at_collection"], "indices": [{"code": q["canonical_code"], "status": q["status"], "source_timestamp": q["source_timestamp"]} for q in snapshot["indices"]]}, ensure_ascii=False))
    return 0 if snapshot["status"] == "market_closed" or (snapshot["status"] == "ok" and snapshot["risk_analysis"]["status"] == "ok") else 2


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
    import socket
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import ValidationError
    from .publication import JSONIntegrityError, SnapshotBlockError, extract_snapshot, strict_json_loads

    class VerificationFailure(Exception):
        def __init__(self, category, reason):
            self.category, self.reason = category, reason

    def dns_failure(exc):
        # requests/urllib3 wrap the original socket error in several layers.
        pending, visited = [exc], set()
        while pending:
            item = pending.pop()
            if id(item) in visited:
                continue
            visited.add(id(item))
            if isinstance(item, socket.gaierror) or type(item).__name__ == "NameResolutionError":
                return True
            if isinstance(item, BaseException):
                pending.extend(item.args)
                pending.extend((item.__cause__, item.__context__, getattr(item, "reason", None)))
        return False

    report = {"checked_at": shanghai_now().isoformat(), "http_verified": False, "json_parsed": False, "public_https_verified": False, "schema_verified": False, "chatgpt_action_verified": False, "live_usable_at_read": False, "reasons": [], "failure_category": None}
    if urlsplit(args.url).scheme != "https" or urlsplit(args.url).username or urlsplit(args.url).password:
        report["reasons"] = ["public_https_url_required"]
        report["failure_category"] = "configuration"
    else:
        try:
            # No authentication, cookies, credentials, proxy credentials or env dumps.
            expected = None
            # A deterministic content hash alone can itself hit an old cache.
            params = {"read_at": str(time.time_ns())}
            if args.expected:
                try:
                    expected = strict_json_loads(Path(args.expected).read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise VerificationFailure("local_input", "expected_snapshot_unreadable_or_invalid") from exc
                params["v"] = hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            response = requests.get(args.url, params=params, timeout=(5, 20), headers={"Cache-Control": "no-cache"}, allow_redirects=False)
            report["http_status"] = response.status_code
            report["content_type"] = response.headers.get("Content-Type", "")
            report["cache_headers"] = {k: response.headers[k] for k in ("Date", "Age", "ETag", "Cache-Control", "Last-Modified") if k in response.headers}
            if response.status_code != 200:
                raise VerificationFailure("http", "http_status_not_200")
            report["http_verified"] = True
            raw = response.content
            if not isinstance(raw, bytes):
                raise VerificationFailure("http", "invalid_response_body_type")
            content_length = response.headers.get("Content-Length")
            if content_length and not response.headers.get("Content-Encoding") and not response.headers.get("Transfer-Encoding"):
                try:
                    if int(content_length) != len(raw):
                        raise VerificationFailure("http", "response_length_mismatch")
                except ValueError as exc:
                    raise VerificationFailure("http", "invalid_content_length") from exc
            if raw.startswith(b"\xef\xbb\xbf"):
                raise VerificationFailure("encoding", "utf8_bom_present")
            text = raw.decode("utf-8", errors="strict")
            if urlsplit(args.url).path.endswith(".html"):
                document = extract_snapshot(text)
            else:
                if text.lstrip().startswith("<"):
                    raise VerificationFailure("json_syntax", "html_response_for_json")
                document = strict_json_loads(text)
            report["json_parsed"] = True
            schema = json.loads((Path(__file__).parents[1] / "schemas" / "snapshot.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
            report["schema_verified"] = True
            if expected is not None:
                report["published_snapshot_matches"] = document == expected
                if document != expected:
                    raise VerificationFailure("integrity", "published_snapshot_mismatch")
            report["public_https_verified"] = True
            report["snapshot_generated_at"] = document["generated_at"]
            report["snapshot_status"] = document["status"]
            report["risk_status"] = document.get("risk_analysis", {}).get("status")
            report.update(assess_public_snapshot(document, shanghai_now()))
            from .risk import assess_risk_at_read
            report.update(assess_risk_at_read(document, shanghai_now()))
            if not report["live_usable_at_read"]:
                report["failure_category"] = "data_validation"
            elif not report["risk_usable_at_read"]:
                report["failure_category"] = "risk_validation"
        except VerificationFailure as exc:
            report["failure_category"] = exc.category
            report["reasons"].append(exc.reason)
        except requests.exceptions.Timeout:
            report["failure_category"] = "transport"
            report["reasons"].append("http_timeout")
        except requests.exceptions.SSLError:
            report["failure_category"] = "transport"
            report["reasons"].append("tls_error")
        except requests.exceptions.ConnectionError as exc:
            report["failure_category"] = "transport"
            report["reasons"].append("dns_error" if dns_failure(exc) else "http_connection_error")
        except requests.exceptions.RequestException:
            report["failure_category"] = "transport"
            report["reasons"].append("http_request_error")
        except UnicodeDecodeError:
            report["failure_category"] = "encoding"
            report["reasons"].append("invalid_utf8")
        except SnapshotBlockError:
            report["failure_category"] = "html_extraction"
            report["reasons"].append("snapshot_json_block_invalid")
        except JSONIntegrityError as exc:
            report["failure_category"] = "json_integrity"
            report["reasons"].append(str(exc))
        except json.JSONDecodeError as exc:
            report["failure_category"] = "json_syntax"
            report["reasons"].append("invalid_json_syntax")
            report["json_error_position"] = {"line": exc.lineno, "column": exc.colno}
        except ValidationError:
            report["failure_category"] = "schema"
            report["reasons"].append("snapshot_schema_invalid")
        except (OSError, ValueError, TypeError, KeyError):
            report["failure_category"] = "internal"
            report["reasons"].append("verification_internal_error")
    write_json(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False))
    if getattr(args, "require_live", False):
        return 0 if report["public_https_verified"] and report.get("live_usable_at_read") and report.get("risk_usable_at_read") else 2
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
    collector.add_argument("--capture-seconds", type=int, choices=range(1, 301), default=120, metavar="1..300", help="Stop network/retries after this many seconds past 14:30; default leaves three minutes for publishing")
    collector.set_defaults(run=collect_command)
    probe = sub.add_parser("live-test")
    probe.add_argument("--output-dir", default="reports/live")
    probe.set_defaults(run=live_test)
    url = sub.add_parser("verify-url")
    url.add_argument("url")
    url.add_argument("--output", default="reports/public-url.json")
    url.add_argument("--expected", help="Compare HTTPS content with this locally published snapshot")
    url.add_argument("--require-live", action="store_true", help="Require fresh quotes AND usable risk; HTTP success alone is insufficient")
    url.set_defaults(run=verify_url)
    local = sub.add_parser("read-local")
    local.add_argument("path", nargs="?", default="public/latest.json")
    local.set_defaults(run=read_local)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
