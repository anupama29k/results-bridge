from results_bridge.validation import validate_rows, summarize

CRIT = {"param_path": ["measurements", "concentration_ng_per_uL"],
        "min": 10.0, "max": 100.0, "unit": "ng/uL"}

def _row(sid, v):
    return {"sample_id": sid, "measurements": {"concentration_ng_per_uL": v}}

def test_pass_fail_invalid():
    res = validate_rows([_row("A", 12.0), _row("B", 5.0), _row("C", 150.0), _row("D", None)], CRIT)
    assert [r["verdict"] for r in res] == ["PASS", "FAIL", "FAIL", "INVALID"]
    assert "minimum" in res[1]["reason"] and "maximum" in res[2]["reason"]

def test_summary_overall():
    counts, overall = summarize(validate_rows([_row("A", 12.0)], CRIT))
    assert overall == "ALL_PASS" and counts == {"PASS": 1, "FAIL": 0, "INVALID": 0}
    counts, overall = summarize(validate_rows([_row("A", 1.0)], CRIT))
    assert overall == "ATTENTION_REQUIRED"
