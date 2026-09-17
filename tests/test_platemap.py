"""M7: plate-map-aware validation.

Part 1 (engine, runs now): unit tests on validate_rows with inline
criteria and maps — no fixtures needed.
Part 2 (integration, skips until Anu's science lands): arms once
criteria.json gives ELISA_001_tmb_endpoint a "roles" block AND the
plate-map fixture tests/fixtures/spark_plate_map.json exists.
"""
import json
import pathlib

import pytest

from results_bridge import tools
from results_bridge.validation import load_criteria, validate_rows, summarize

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

CRIT = {"param_path": ["measurements", "absorbance"], "unit": "AU",
        "min": 0.1, "max": 3.5,
        "roles": {"blank": {"max": 0.1},
                  "sample": {"min": 0.1, "max": 3.5},
                  "empty": {"skip": True}}}

def _row(well, v):
    return {"sample_id": well, "well_position": well,
            "measurements": {"absorbance": v}}


# ---------- Part 1: engine (green immediately) ----------

def test_same_value_opposite_verdicts_by_role():
    rows = [_row("A1", 0.06), _row("B1", 0.06)]
    res = validate_rows(rows, CRIT, {"A1": "blank", "B1": "sample"})
    by = {r["sample_id"]: r for r in res}
    assert by["A1"]["verdict"] == "PASS"   # a blank SHOULD read low
    assert by["B1"]["verdict"] == "FAIL"   # a sample must not
    assert by["B1"]["role"] == "sample" and "for role 'sample'" in by["B1"]["reason"]

def test_blank_above_background_fails():
    res = validate_rows([_row("A1", 0.25)], CRIT, {"A1": "blank"})
    assert res[0]["verdict"] == "FAIL"     # contaminated blank

def test_one_sided_rule_has_no_floor():
    # blank rule is max-only: even 0.0 passes
    res = validate_rows([_row("A1", 0.0)], CRIT, {"A1": "blank"})
    assert res[0]["verdict"] == "PASS"

def test_unmapped_well_defaults_to_sample():
    res = validate_rows([_row("H12", 1.0)], CRIT, {"A1": "blank"})
    assert res[0]["role"] == "sample" and res[0]["verdict"] == "PASS"

def test_skip_role_excluded_from_results():
    res = validate_rows([_row("A1", 0.5), _row("A2", 0.5)], CRIT, {"A2": "empty"})
    assert [r["sample_id"] for r in res] == ["A1"]

def test_no_map_behaves_exactly_as_before():
    res = validate_rows([_row("A1", 0.06)], CRIT)  # no plate map
    assert res[0]["verdict"] == "FAIL" and res[0]["role"] is None


# ---------- Part 2: integration (Anu's science arms it) ----------

def _science_landed() -> bool:
    crit = load_criteria().get("ELISA_001_tmb_endpoint", {})
    return "roles" in crit and (FIXTURES / "spark_plate_map.json").exists()

science = pytest.mark.skipif(
    not _science_landed(),
    reason="M7 science pending — add a 'roles' block to ELISA_001 in criteria.json "
           "and create tests/fixtures/spark_plate_map.json",
)

@science
def test_spark_plate_with_map_all_pass():
    out = tools.check_file(str(FIXTURES / "test_tecan_spark.xlsx"),
                           "ELISA_001_tmb_endpoint",
                           str(FIXTURES / "spark_plate_map.json"))
    assert out["summary"]["FAIL"] == 0
    assert out["overall"] == "ALL_PASS"    # the 5 'failures' were blanks doing their job

@science
def test_spark_plate_without_map_unchanged():
    out = tools.check_file(str(FIXTURES / "test_tecan_spark.xlsx"),
                           "ELISA_001_tmb_endpoint")
    assert out["summary"] == {"PASS": 91, "FAIL": 5, "INVALID": 0}
