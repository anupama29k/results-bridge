"""M8: plate-level acceptance scoring.

Part 1 -- engine, on synthetic rows (no files).
Part 2 -- end to end through tools.check_file and /ingest on Tecan Spark
          exports built by fixtures/make_plate_fixtures.py.
Part 3 -- drift guards against the robotic-assays library.
"""
import copy
import json
import math
import pathlib

import pytest
from fastapi.testclient import TestClient

from results_bridge import audit, tools
from results_bridge.main import app
from results_bridge.plate_scoring import (fit_model, inverse, normalize_plate_map,
                                          score_plate)
from results_bridge.validation import combine_overall, load_criteria, validate_rows

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ROWS = "ABCDEFGH"


# ───────────────────────────── helpers ──────────────────────────────

def linear_plate(slope=0.001, intercept=0.08, noise=None):
    """8-pt standard in duplicate (cols 1-2, H = blank), 4 samples in duplicate."""
    pm, vals = {}, {}
    concs = [2000 / 2 ** i for i in range(7)]
    for c in (1, 2):
        for i, r in enumerate(ROWS):
            if r == "H":
                pm[f"H{c}"] = {"role": "blank", "conc": 0}
                vals[f"H{c}"] = intercept
            else:
                pm[f"{r}{c}"] = {"role": "standard", "conc": concs[i], "group": f"STD{i + 1}"}
                vals[f"{r}{c}"] = intercept + slope * concs[i]
    for i, x in enumerate([150, 400, 900, 1600]):
        for c in (3, 4):
            w = f"{ROWS[i]}{c}"
            pm[w] = {"role": "sample", "group": f"S{i + 1}", "dilution": 1}
            vals[w] = intercept + slope * x
    if noise:
        for w, f in noise.items():
            vals[w] *= f
    return pm, vals


PLATE_CFG = {
    "model": "linear", "blank_subtract": True, "conc_unit": "ug/mL",
    "criteria": [
        {"key": "standard_curve_r2", "type": "curve_r2", "min": 0.995},
        {"key": "blank_od562_max", "type": "blank_mean_max", "max": 0.10},
        {"key": "duplicate_cv_max", "type": "replicate_cv_max", "max": 5.0},
    ],
}


def by_key(plate):
    return {c["key"]: c for c in plate["criteria"]}


# ───────────────────────────── Part 1: engine ───────────────────────

def test_plate_map_accepts_m7_and_m8_forms_and_ignores_meta():
    wells, meta = normalize_plate_map({"_meta": {"conc_unit": "ng/mL"},
                                       "A1": "blank", "A2": {"role": "standard", "conc": 5}})
    assert wells == {"A1": {"role": "blank"}, "A2": {"role": "standard", "conc": 5}}
    assert meta == {"conc_unit": "ng/mL"}


def test_plate_map_rejects_entries_without_role():
    with pytest.raises(ValueError):
        normalize_plate_map({"A1": {"conc": 5}})


def test_well_validation_reads_role_from_m8_dict():
    crit = {"param_path": ["v"], "unit": "AU", "min": 0.1, "max": 3.5,
            "roles": {"blank": {"max": 0.1}}}
    rows = [{"sample_id": "A1", "well_position": "A1", "v": 0.06}]
    res = validate_rows(rows, crit, {"A1": {"role": "blank", "conc": 0}})
    assert res[0]["role"] == "blank" and res[0]["verdict"] == "PASS"


@pytest.mark.parametrize("model,params", [
    ("linear", None), ("quadratic", None), ("4pl", (0.05, 1.2, 0.1, 2.8))])
def test_fit_and_inverse_round_trip(model, params):
    xs = [1.0 / 2 ** i for i in range(7)] if model == "4pl" else [2000 / 2 ** i for i in range(7)]
    if model == "linear":
        f = lambda x: 0.08 + 0.001 * x
    elif model == "quadratic":
        f = lambda x: 0.09 + 1.05e-3 * x - 1.6e-7 * x * x
    else:
        a, b, c, d = params
        f = lambda x: d + (a - d) / (1 + (x / c) ** b)
    fit = fit_model(model, xs, [f(x) for x in xs])
    assert fit["r2"] > 0.9999
    for x in xs[1:-1]:
        assert inverse(fit, f(x), (min(xs), max(xs))) == pytest.approx(x, rel=0.01)


def test_clean_plate_passes_and_back_calculates():
    pm, vals = linear_plate()
    plate = score_plate(vals, pm, PLATE_CFG)
    assert plate["verdict"] == "PASS"
    s = {x["group"]: x for x in plate["samples"]}
    assert s["S2"]["concentration"] == pytest.approx(400, rel=1e-3)
    assert plate["blank_subtracted"] is True


def test_no_standards_is_not_evaluated_and_leaves_overall_unchanged():
    plate = score_plate({"A1": 0.5}, {"A1": "blank"}, PLATE_CFG)
    assert plate["verdict"] == "NOT_EVALUATED"
    assert combine_overall("ALL_PASS", plate) == "ALL_PASS"


def test_missing_qc_wells_make_the_plate_incomplete_never_pass():
    pm, vals = linear_plate()
    cfg = copy.deepcopy(PLATE_CFG)
    cfg["criteria"].append({"key": "qc_sample_recovery", "type": "qc_recovery", "min": 90, "max": 110})
    plate = score_plate(vals, pm, cfg)
    assert by_key(plate)["qc_sample_recovery"]["verdict"] == "NOT_EVALUATED"
    assert plate["verdict"] == "INCOMPLETE"
    assert combine_overall("ALL_PASS", plate) == "ATTENTION_REQUIRED"


def test_contaminated_blank_fails_by_name():
    pm, vals = linear_plate(noise={"H1": 3.0, "H2": 3.0})
    plate = score_plate(vals, pm, PLATE_CFG)
    assert by_key(plate)["blank_od562_max"]["verdict"] == "FAIL"
    assert plate["verdict"] == "FAIL"


def test_one_bad_duplicate_fails_cv_and_names_the_group():
    pm, vals = linear_plate(noise={"B3": 1.25})          # sample S2, one replicate 25% high
    crit = by_key(score_plate(vals, pm, PLATE_CFG))["duplicate_cv_max"]
    assert crit["verdict"] == "FAIL"
    assert list(crit["detail"]["failing_groups"]) == ["S2"]


def test_too_few_standards_is_invalid_with_reason():
    pm = {"A1": {"role": "standard", "conc": 10}, "H1": {"role": "blank", "conc": 0}}
    plate = score_plate({"A1": 0.5, "H1": 0.05}, pm, PLATE_CFG)
    r2 = by_key(plate)["standard_curve_r2"]
    assert r2["verdict"] == "INVALID" and "distinct concentrations" in r2["reason"]
    assert plate["verdict"] == "INVALID"


def test_standard_without_concentration_is_rejected():
    with pytest.raises(ValueError, match="no 'conc'"):
        score_plate({"A1": 0.5}, {"A1": {"role": "standard"}}, PLATE_CFG)


def test_unknown_criterion_type_is_invalid_not_silently_skipped():
    pm, vals = linear_plate()
    cfg = copy.deepcopy(PLATE_CFG)
    cfg["criteria"].append({"key": "typo", "type": "curve_rr2", "min": 0.9})
    assert score_plate(vals, pm, cfg)["verdict"] == "INVALID"


def test_signal_above_flag_and_out_of_range_sample():
    pm, vals = linear_plate()
    vals["D3"] = vals["D4"] = 2.5                          # S4 far above the top standard
    cfg = copy.deepcopy(PLATE_CFG)
    cfg["flags"] = [{"type": "signal_above", "value": 2.0, "message": "re-run diluted"}]
    flags = score_plate(vals, pm, cfg)["flags"]
    assert {"signal_above", "outside_standard_range"} <= {f["type"] for f in flags}


# ───────────────────── Part 2: end to end on Spark files ─────────────

def test_elisa_plate_passes_every_library_criterion():
    r = tools.check_file(str(FIXTURES / "elisa_spark_plate.xlsx"), "ELISA_001_tmb_endpoint",
                         str(FIXTURES / "elisa_plate_map.json"))
    plate = r["plate"]
    assert plate["verdict"] == "PASS" and r["overall"] == "ALL_PASS"
    crit = by_key(plate)
    assert set(crit) == {"standard_curve_r2", "curve_bottom_asymptote_od", "curve_top_asymptote_od",
                         "intraplate_cv_max", "qc_sample_recovery", "ppc_recovery"}
    assert 80 <= crit["qc_sample_recovery"]["value"] <= 120
    assert 70 <= crit["ppc_recovery"]["value"] <= 130
    assert plate["fit"]["model"] == "4pl"
    assert any(f["type"] == "outside_standard_range" for f in plate["flags"])  # one sample above curve
    assert audit.recent(1)[0]["plate_verdict"] == "PASS"


def test_bca_plate_fails_the_library_linear_rule_but_not_on_curvature_alone():
    """Realistic BCA (curvilinear) data: blank, CV and QC pass; the library's
    LINEAR R2 >= 0.995 fails. The alternative fits are reported so the method
    owner can see that a quadratic / 4PL model would pass."""
    r = tools.check_file(str(FIXTURES / "bca_spark_plate.xlsx"), "PROT_002_bca_od562",
                         str(FIXTURES / "bca_plate_map.json"))
    plate = r["plate"]
    crit = by_key(plate)
    assert crit["standard_curve_r2"]["verdict"] == "FAIL"
    assert all(crit[k]["verdict"] == "PASS"
               for k in ("blank_od562_max", "duplicate_cv_max", "qc_sample_recovery"))
    assert plate["alternative_fits_r2"]["quadratic"] >= 0.995
    assert r["overall"] == "ATTENTION_REQUIRED"


def test_bca_plate_passes_when_the_method_owner_selects_quadratic():
    from results_bridge.parsers import parse_instrument_file
    from results_bridge.validation import plate_values
    crit = load_criteria()["PROT_002_bca_od562"]
    cfg = copy.deepcopy(crit["plate"]); cfg["model"] = "quadratic"
    _, rows = parse_instrument_file("bca_spark_plate.xlsx", (FIXTURES / "bca_spark_plate.xlsx").read_bytes())
    pm = json.loads((FIXTURES / "bca_plate_map.json").read_text())
    assert score_plate(plate_values(rows, crit), pm, cfg)["verdict"] == "PASS"


def test_role_only_plate_map_keeps_m7_behaviour():
    r = tools.check_file(str(FIXTURES / "test_tecan_spark.xlsx"), "ELISA_001_tmb_endpoint",
                         str(FIXTURES / "spark_plate_map.json"))
    assert r["plate"]["verdict"] == "NOT_EVALUATED"
    assert r["overall"] == ("ALL_PASS" if r["summary"]["FAIL"] == r["summary"]["INVALID"] == 0
                            else "ATTENTION_REQUIRED")


def test_ingest_scores_the_plate_when_given_a_plate_map():
    client = TestClient(app)
    data = (FIXTURES / "elisa_spark_plate.xlsx").read_bytes()
    r = client.post("/ingest",
                    data={"assay_id": "ELISA_001_tmb_endpoint",
                          "plate_map": (FIXTURES / "elisa_plate_map.json").read_text()},
                    files={"file": ("elisa_spark_plate.xlsx", data, "application/octet-stream")})
    assert r.status_code == 200 and r.json()["plate"]["verdict"] == "PASS"
    bad = client.post("/ingest", data={"assay_id": "ELISA_001_tmb_endpoint", "plate_map": "{nope"},
                      files={"file": ("elisa_spark_plate.xlsx", data, "application/octet-stream")})
    assert bad.status_code == 422


# ─────────────────────── Part 3: drift guards ────────────────────────

def _library_entry(assay_id):
    from results_bridge.library import load_all_assays
    hits = [e for e in load_all_assays() if e.get("assay_id") == assay_id]
    return hits[-1] if hits else None


@pytest.mark.parametrize("assay_id", [k for k, v in load_criteria().items() if "plate" in v])
def test_every_plate_criterion_restates_a_library_criterion(assay_id):
    plate = load_criteria()[assay_id]["plate"]
    entry = _library_entry(plate["library_assay_id"])
    assert entry is not None, f"{plate['library_assay_id']} not in the library"
    prose = set(entry["acceptance_criteria"])
    for c in plate["criteria"]:
        assert c["key"] in prose, f"{assay_id}: {c['key']} is not a {plate['library_assay_id']} criterion"


@pytest.mark.parametrize("assay_id,fixture", [("BIO_002", "bca_plate_map.json"),
                                              ("BIO_005", "elisa_plate_map.json")])
def test_fixture_layout_matches_the_library_generated_plate_map(assay_id, fixture):
    """The fixtures reuse the codegen layout; QC / spike wells replace samples."""
    from results_bridge.parsers import _LIB
    lib_map = pathlib.Path(_LIB) / "protocols" / "opentrons" / "plate_maps" / f"{assay_id}_plate_map.json"
    if not lib_map.exists():
        pytest.skip("library has no generated plate maps yet (robotic-assays opentrons codegen)")
    lib = json.loads(lib_map.read_text())
    fix = json.loads((FIXTURES / fixture).read_text())
    assert lib["_meta"]["conc_unit"] == fix["_meta"]["conc_unit"]
    for well, entry in lib.items():
        if well.startswith("_"):
            continue
        if entry["role"] in ("standard", "blank"):
            assert fix[well] == entry, well
        else:
            assert fix[well]["role"] in ("sample", "qc", "positive_control"), well
            assert fix[well].get("dilution") == entry.get("dilution"), well
