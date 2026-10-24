"""Read-time reminder contract. External research can never populate formal risk."""
from .core import assess_public_snapshot, timestamp
from .risk import assess_risk_at_read
import json
from pathlib import Path


def compose_reminder(snapshot, research, now):
    now = timestamp(now)
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).parents[1] / "schemas/snapshot.schema.json").read_text(encoding="utf-8"))
    schema_errors = list(Draft202012Validator(schema).iter_errors(snapshot))
    if schema_errors:
        snapshot = {"status": "unavailable", "data_kind": "unavailable"}
    quote_gate = assess_public_snapshot(snapshot, now)
    risk_gate = assess_risk_at_read(snapshot, now)
    valid = quote_gate["live_usable_at_read"] and risk_gate["risk_usable_at_read"]
    formal = {"status": "ok" if valid else "unavailable", "read_at": now.isoformat(), "source": "system_original_snapshot", "indices": [], "reasons": quote_gate["reasons"] + risk_gate["risk_reasons"]}
    if valid:
        formal["indices"] = snapshot["risk_analysis"]["indices"]
    evidence = {"status": "unavailable", "reason": "research_missing_future_or_wrong_scope"}
    try:
        if research["schema_version"] == "market_evidence.v1" and timestamp(research["generated_at"]) <= now and research["scope"] == ["000510", "399673"] and research["formal_risk_substitute"] is False:
            evidence = research
    except (KeyError, TypeError, ValueError):
        pass
    if schema_errors:
        formal["reasons"].append("complete_json_schema_invalid")
    return {"read_at": now.isoformat(), "formal_intraday_risk": formal, "market_evidence_and_trend": evidence, "web_fallback_required": not valid, "fallback_rule": "Report actual read failure. Retrieve cited public pages with actual source time; if only prior close is verified label it as prior close. Never label fallback no_trigger/watch/elevated or 14:30 formal risk.", "today_close_or_final_etf_flows_known": False, "trading_automation": False}
