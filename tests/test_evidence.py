from copy import deepcopy
import json

import pytest

from etf_assistant.core import INSTRUMENTS, build_snapshot, timestamp
from etf_assistant.evidence import append_record, auxiliary, build_evidence, calendar_events, read_chain, validated_daily
from etf_assistant.reminder import compose_reminder
from scripts.render_market_evidence import render_market_evidence


NOW = timestamp("2026-10-23T14:33:00+08:00")


def source(code="000510", name="中证A500"):
    return {"source_id": "test-source-" + code, "url": "https://push2his.eastmoney.com/api/qt/stock/kline/get", "provider": "eastmoney", "received_at": "2026-10-23T13:17:00+08:00", "source_timestamp": "2026-10-22T15:00:00+08:00", "response_sha256": "a" * 64, "identity_verified": True, "identity_origin": "same_daily_response", "source_code": code, "source_name": name}


def bundle():
    result = {"calendar": {"dates": ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"]}, "indices": {}}
    for spec in INSTRUMENTS:
        result["indices"][spec.code] = {"source": source(spec.code, spec.name), "daily": [{"date": "2026-10-21", "open": 100, "high": 105, "low": 98, "close": 100, "volume": 100, "amount": 1000}, {"date": "2026-10-22", "open": 100, "high": 104, "low": 99, "close": 102 if spec.code == "000510" else 101, "volume": 110, "amount": 1100}]}
    return result


def test_append_chain_preserves_bytes_idempotent_and_detects_rewrite(tmp_path):
    path = tmp_path / "history.jsonl"
    append_record(path, "one", {"value": 1}, NOW.isoformat())
    original = path.read_bytes()
    append_record(path, "one", {"value": 1}, NOW.isoformat())
    assert path.read_bytes() == original
    append_record(path, "two", {"value": 2}, NOW.isoformat())
    assert path.read_bytes().startswith(original)
    assert read_chain(path)[1]["previous_hash"] == read_chain(path)[0]["record_hash"]
    with pytest.raises(ValueError, match="evidence_id_conflict"):
        append_record(path, "one", {"value": 3}, NOW.isoformat())
    path.write_text(path.read_text().replace('"value": 1', '"value": 9'), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence_chain_corrupt"):
        read_chain(path)


def test_two_indices_history_and_relative_performance_are_evidence_not_formal(tmp_path):
    doc = build_evidence(bundle(), tmp_path, NOW)
    assert doc["status"] == "available_with_gaps"
    assert doc["relative_performance"]["growth_minus_broad_market_pp"] == pytest.approx(-1)
    assert doc["formal_risk_substitute"] is False
    assert doc["etf_flows"]["status"] == "unavailable"
    for index in doc["indices"]:
        assert index["thesis_status"] == "watch_evidence_incomplete"
        assert index["confidence"] == "limited"
        assert len(read_chain(tmp_path / "daily" / (index["code"] + ".jsonl"))) == 2
        assert len(read_chain(tmp_path / "thesis" / (index["code"] + ".jsonl"))) == 1
        assert "probability" not in index["scenarios"]
        assert len(index["scenarios"]["cases"]) == 3


def test_same_fetch_does_not_overwrite_daily_and_revision_is_appended(tmp_path):
    packet = bundle()
    build_evidence(packet, tmp_path, NOW)
    path = tmp_path / "daily/000510.jsonl"
    original = path.read_bytes()
    build_evidence(packet, tmp_path, NOW)
    assert path.read_bytes() == original
    packet["indices"]["000510"]["daily"][-1]["close"] = 103
    build_evidence(packet, tmp_path, NOW)
    assert path.read_bytes().startswith(original)
    assert len(read_chain(path)) == 3


def test_intraday_daily_bar_and_future_close_never_known_at_1433():
    item = bundle()["indices"]["000510"]
    item["daily"].append(dict(item["daily"][-1], date="2026-10-23"))
    _, rows = validated_daily(INSTRUMENTS[0], item, NOW)
    assert len(rows) == 2
    assert rows[-1]["date"] == "2026-10-22"


@pytest.mark.parametrize("key,value", [("source_code", "000300"), ("source_name", "创业板指"), ("identity_origin", "separate_quote"), ("identity_verified", False), ("received_at", "2026-10-23T14:34:00+08:00")])
def test_daily_identity_and_future_retrieval_rejected(key, value):
    item = bundle()["indices"]["000510"]
    item["source"][key] = value
    with pytest.raises(ValueError):
        validated_daily(INSTRUMENTS[0], item, NOW)


def flow(date="2026-10-22", measurement="share_change_times_nav_estimate"):
    return {"observations": [{"date": date, "published_at": "2026-10-23T08:30:00+08:00", "verification": "source_verified", "universe": "explicit test ETF basket", "methodology": "split-adjusted share delta times previous NAV", "measurement": measurement, "unit": "CNY", "value": 1000, "source": source()}]}


@pytest.mark.parametrize("date,measurement", [("2026-10-23", "net_subscription"), ("2026-10-22", "turnover"), ("2026-10-21", "net_subscription")])
def test_today_final_flow_turnover_or_old_day_cannot_fill_prior_day(date, measurement):
    assert auxiliary(flow(date, measurement), "etf_flows", "2026-10-22", NOW)["status"] == "unavailable"


def test_etf_estimate_retains_method_and_incompatible_sources():
    raw = flow()
    raw["observations"].append(dict(raw["observations"][0], value=2000, universe="different ETF basket"))
    result = auxiliary(raw, "etf_flows", "2026-10-22", NOW)
    assert len(result["observations"]) == 2
    assert result["observations"][0]["is_estimate"] is True
    assert "none_" in result["aggregation"]


def event():
    return {"event_id": "test-event", "title": "Official planned release", "category": "macro_policy", "date_certainty": "confirmed", "published_at": "2025-12-25T00:00:00+08:00", "source_excerpt": "annual planned calendar", "observation": "subject to revision", "thesis_link": "macro", "source": dict(source(), url="https://www.stats.gov.cn/calendar"), "verification": "source_verified", "date": "2026-11-09"}


def test_calendar_never_promotes_window_to_confirmed_or_accepts_unreviewed_source():
    raw = event()
    raw.update(date_certainty="estimated_window", date="2026-12-14", window={"start": "2026-12-01", "end": "2026-12-31"})
    result = calendar_events([raw], NOW)[0]
    assert result["date"] is None
    assert result["window"] == raw["window"]
    bad = event()
    bad["source"]["url"] = "https://fake-stats.gov.cn.example.org/calendar"
    assert calendar_events([bad], NOW) == []
    bad = event()
    bad["verification"] = "unverified"
    assert calendar_events([bad], NOW) == []


def test_stale_daily_and_calendar_gap_do_not_create_high_confidence(tmp_path):
    raw = bundle()
    del raw["indices"]["000510"]["daily"][-1]
    doc = build_evidence(raw, tmp_path, NOW)
    assert doc["indices"][0]["status"] == "stale_history"
    assert doc["relative_performance"]["status"] == "unavailable"
    assert all(x["confidence"] != "high" for x in doc["indices"])


def test_complete_json_failure_and_rich_research_cannot_forge_formal_risk(tmp_path):
    research = build_evidence(bundle(), tmp_path, NOW)
    snapshot = build_snapshot({}, {}, NOW)
    snapshot["risk_analysis"]["status"] = "ok"
    result = compose_reminder(snapshot, research, NOW)
    assert result["formal_intraday_risk"]["status"] == "unavailable"
    assert result["formal_intraday_risk"]["indices"] == []
    assert result["web_fallback_required"] is True
    assert result["market_evidence_and_trend"]["relative_performance"]["status"] == "available"
    del snapshot["risk_analysis"]
    result = compose_reminder(snapshot, research, NOW)
    assert "complete_json_schema_invalid" in result["formal_intraday_risk"]["reasons"]
    assert result["today_close_or_final_etf_flows_known"] is False


def test_research_html_escapes_source_text_and_has_time_and_all_scenarios(tmp_path):
    raw = bundle()
    doc = build_evidence(raw, tmp_path, NOW)
    doc["gaps"].append('<script>alert(1)</script>')
    html = render_market_evidence(doc)
    assert '<script>' not in html
    assert "2026-10-22T15:00:00+08:00" in html
    for word in ("企稳", "震荡", "继续走弱", "口径", "未核实"):
        assert word in html


def test_valid_original_risk_is_reported_and_late_read_cannot_reuse_it():
    from datetime import timedelta
    from test_risk import NOW as CAPTURED, CALENDAR, packets
    snapshot = build_snapshot(CALENDAR, packets(), CAPTURED)
    good = compose_reminder(snapshot, {}, CAPTURED + timedelta(minutes=3))
    assert good["formal_intraday_risk"]["status"] == "ok"
    assert good["formal_intraday_risk"]["indices"] == snapshot["risk_analysis"]["indices"]
    late = compose_reminder(snapshot, {}, CAPTURED + timedelta(minutes=6))
    assert late["formal_intraday_risk"]["status"] == "unavailable"


def test_official_timestamp_schedule_retains_time_and_date():
    raw = event()
    raw["date"] = "2026-11-09T09:30:00+08:00"
    result = calendar_events([raw], NOW)[0]
    assert result["date"] == "2026-11-09"
    assert result["scheduled_at"] == raw["date"]


def test_delivery_after_reminder_is_explicit_failure_even_inside_fresh_window():
    from scripts.check_delivery import check
    report = {"public_https_verified": True, "published_snapshot_matches": True, "live_usable_at_read": True, "risk_usable_at_read": True, "checked_at": "2026-10-23T14:34:00+08:00"}
    with pytest.raises(ValueError, match="1433"):
        check({"status": "ok", "target_at": "2026-10-23T14:30:00+08:00"}, [report])
    report["checked_at"] = "2026-10-23T14:32:50+08:00"
    check({"status": "ok", "target_at": "2026-10-23T14:30:00+08:00"}, [report])


def test_publication_stages_tracked_verification_before_rebase(tmp_path):
    import subprocess
    import shlex
    from pathlib import Path
    import yaml
    from etf_assistant.cli import write_json
    from scripts.prepare_publication import prepare
    from test_risk import NOW as CAPTURED, CALENDAR, packets
    public = tmp_path / "public"
    public.mkdir()
    (public / "verification.json").write_text('{"old":true}', encoding="utf-8")
    run = lambda *args: subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)
    run("init")
    run("add", "public/verification.json")
    run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "prior publication")
    snapshot = build_snapshot(CALENDAR, packets(), CAPTURED)
    write_json(tmp_path / "new.json", snapshot)
    prepare(tmp_path / "new.json", public, CAPTURED, CAPTURED)
    (public / "openapi.json").write_text('{}', encoding="utf-8")
    (public / "market-evidence.json").write_text('{}', encoding="utf-8")
    (public / "market-evidence.html").write_text('research', encoding="utf-8")
    (public / "research").mkdir()
    (public / "research/test.jsonl").write_text('{}\n', encoding="utf-8")
    workflow = yaml.load((Path(__file__).parents[1] / ".github/workflows/collect.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    publish = next(step for step in workflow["jobs"]["collect"]["steps"] if step.get("id") == "publish")
    for line in publish["run"].splitlines():
        if line.startswith("git add "):
            run(*shlex.split(line)[1:])
    assert run("diff", "--name-only").stdout.strip() == ""
