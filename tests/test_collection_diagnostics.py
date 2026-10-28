from scripts.report_collection import summarize


def test_diagnostics_report_future_bars_and_distinguish_upstream_errors():
    raw = {"collection_attempts": [
        {"generated_at": "2026-10-27T14:31:52+08:00",
         "status": "unavailable", "risk_status": "unavailable",
         "packets": {
             "000510": {"rows": [{"time": "2026-10-27 14:30"},
                                    {"time": "2026-10-27 14:35"}],
                        "attempts": [{"market": 1, "period_minutes": 5,
                                      "quote_status": "future_source_timestamp"}]},
             "399673": {"error": "all_minute_sources_unavailable",
                        "attempts": [{"market": 0, "period_minutes": 1,
                                      "error": "source_call_failed",
                                      "error_type": "JSONDecodeError",
                                      "private_detail": "must not be printed"}]}}}]}
    result = summarize(raw)["collection_attempts"][0]
    first = result["indices"]["000510"]
    second = result["indices"]["399673"]
    assert first["future_bar_count"] == 1
    assert first["first_future_bar"] == "2026-10-27T14:35:00+08:00"
    assert second["attempts"][0]["error_type"] == "JSONDecodeError"
    assert "private_detail" not in second["attempts"][0]


def test_diagnostics_handle_absent_or_invalid_times():
    raw = {"collection_attempts": [
        {"generated_at": "not a date", "packets": {
            "000510": {"rows": [{"time": "bad"}, {}], "attempts": []}}}]}
    assert "future_bar_count" not in summarize(raw)["collection_attempts"][0]["indices"]["000510"]
