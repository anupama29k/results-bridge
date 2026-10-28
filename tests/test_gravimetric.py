"""M9: gravimetric liquid-class verification."""
import pathlib

import pytest
from fastapi.testclient import TestClient

from results_bridge import audit, tools
from results_bridge.gravimetric import (load_config, parse_balance_stream, parse_template_csv,
                                        score_gravimetric, stream_to_readings, water_density,
                                        z_factor)
from results_bridge.main import app

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ─────────────────────────── physics vs ISO ───────────────────────────

def test_water_density_matches_reference_values():
    assert water_density(4.0) == pytest.approx(0.999975, abs=2e-6)   # density maximum
    assert water_density(20.0) == pytest.approx(0.998207, abs=2e-6)
    assert water_density(25.0) == pytest.approx(0.997048, abs=2e-6)


@pytest.mark.parametrize("t,z_iso", [(15.0, 1.0020), (20.0, 1.0029), (25.0, 1.0040)])
def test_z_factor_matches_iso_8655_6_table_at_101_3_kpa(t, z_iso):
    assert z_factor(water_density(t)) == pytest.approx(z_iso, abs=1e-4)


# ─────────────────────────── template CSV ─────────────────────────────

def by(result):
    return {(r["liquid"], r["target_uL"]): r for r in result["liquid_classes"]}


def test_template_fixture_gives_the_constructed_verdicts():
    r = tools.check_gravimetric(str(FIXTURES / "gravimetric_template.csv"))
    rows = by(r)
    assert r["format"] == "gravimetric_template_csv"
    assert rows[("water", 20.0)]["verdict"] == "PASS"
    assert rows[("water", 20.0)]["dev_pct"] == pytest.approx(-1.0, abs=0.01)
    assert rows[("water", 20.0)]["cv_pct"] == pytest.approx(1.5, abs=0.01)
    assert rows[("water", 50.0)]["verdict"] == "PASS"
    assert rows[("water", 200.0)]["verdict"] == "FAIL" and "DEV" in rows[("water", 200.0)]["reason"]
    assert rows[("DMSO", 20.0)]["verdict"] == "FAIL"
    assert rows[("BCA_working_reagent", 200.0)]["verdict"] == "PASS"
    assert r["summary"] == {"PASS": 3, "FAIL": 2, "INVALID": 1}
    assert r["overall"] == "ATTENTION_REQUIRED"
    assert audit.recent(1)[0]["assay"] == "GRAVIMETRIC_liquid_class"


def test_unknown_liquid_is_invalid_never_converted_as_water():
    serum = by(tools.check_gravimetric(str(FIXTURES / "gravimetric_template.csv")))[("serum", 50.0)]
    assert serum["verdict"] == "INVALID" and "no density" in serum["reason"]


def test_header_density_override_makes_an_unknown_liquid_scorable(tmp_path):
    text = (FIXTURES / "gravimetric_template.csv").read_text()
    p = tmp_path / "with_serum.csv"
    p.write_text("# density_serum: 1.025\n" + text)
    assert by(tools.check_gravimetric(str(p)))[("serum", 50.0)]["verdict"] == "PASS"


def test_density_changes_the_volume():
    rs = [{"liquid": "DMSO", "target_uL": 20.0, "channel": "1", "replicate": str(i),
           "mass_mg": 22.0, "temperature_C": 21.0} for i in range(10)]
    cfg = load_config()
    dmso = score_gravimetric(rs, cfg)["liquid_classes"][0]["mean_volume_uL"]
    for r in rs:
        r["liquid"] = "water"
    water = score_gravimetric(rs, cfg)["liquid_classes"][0]["mean_volume_uL"]
    assert dmso == pytest.approx(22.0 * z_factor(1.100), abs=1e-3)   # reported to 4 dp
    assert water > dmso * 1.09                     # same mass, ~10% more volume as water


def test_missing_temperature_is_invalid():
    rs = [{"liquid": "water", "target_uL": 50.0, "channel": "1", "replicate": str(i),
           "mass_mg": 50.0, "temperature_C": None} for i in range(10)]
    row = score_gravimetric(rs, load_config())["liquid_classes"][0]
    assert row["verdict"] == "INVALID" and "temperature" in row["reason"]


def test_too_few_replicates_is_invalid():
    rs = [{"liquid": "water", "target_uL": 50.0, "channel": "1", "replicate": str(i),
           "mass_mg": 49.9, "temperature_C": 21.0} for i in range(4)]
    row = score_gravimetric(rs, load_config())["liquid_classes"][0]
    assert row["verdict"] == "INVALID" and "< 10 required" in row["reason"]


def test_channels_are_scored_separately_and_outliers_flagged():
    rs = []
    for ch in ("1", "2"):
        for i in range(12):
            m = 49.85 + (0.02 if i % 2 else -0.02)
            if ch == "2" and i == 5:
                m = 52.0                           # one bad dispense on channel 2
            rs.append({"liquid": "water", "target_uL": 50.0, "channel": ch, "replicate": str(i + 1),
                       "mass_mg": m, "temperature_C": 21.0})
    res = score_gravimetric(rs, load_config())
    assert [r["channel"] for r in res["liquid_classes"]] == ["1", "2"]
    assert res["flags"] and res["flags"][0]["channel"] == "2" and res["flags"][0]["replicates"] == ["6"]


def test_template_needs_liquid_target_and_mass():
    with pytest.raises(ValueError, match="liquid"):
        parse_template_csv("target_uL,mass_mg\n50,49.9\n")
    with pytest.raises(ValueError, match="mass"):
        parse_template_csv("liquid,target_uL\nwater,50\n")


def test_weight_plus_unit_columns_labx_style():
    meta, rs = parse_template_csv("Liquid,Target_uL,Weight,Unit\nwater,50,0.04987,g\n")
    assert rs[0]["mass_mg"] == pytest.approx(49.87)


# ─────────────────────────── balance streams ──────────────────────────

def test_sics_cumulative_stream_auto_detected():
    r = tools.check_gravimetric(str(FIXTURES / "balance_sics_cumulative.txt"), temperature_C=21.0,
                                liquid="water", target_uL=100)
    row = r["liquid_classes"][0]
    assert r["format"] == "balance_stream_cumulative" and row["n"] == 10
    assert row["dev_pct"] == pytest.approx(0.5, abs=0.02) and row["verdict"] == "PASS"


def test_sartorius_individual_stream_auto_detected():
    r = tools.check_gravimetric(str(FIXTURES / "balance_sartorius_individual.txt"), temperature_C=21.0,
                                liquid="water", target_uL=50)
    row = r["liquid_classes"][0]
    assert r["format"] == "balance_stream_individual"
    assert row["dev_pct"] == pytest.approx(-0.4, abs=0.05) and row["verdict"] == "PASS"


def test_stream_without_liquid_and_target_is_rejected():
    with pytest.raises(ValueError, match="pass liquid and target_uL"):
        tools.check_gravimetric(str(FIXTURES / "balance_sics_cumulative.txt"), temperature_C=21.0)


def test_stream_parser_ignores_unstable_and_noise_lines():
    vals = parse_balance_stream("S D 0.01 g\nS I 0.02 g\nhello\nS S 0.05000 g\n")
    assert vals == [50.0]                          # only stable 'S S'; dynamic 'S D' and 'S I' skipped


def test_explicit_mode_overrides_auto():
    used, rs = stream_to_readings([0.0, 50.0, 100.0, 150.0], "water", 50, mode="individual")
    assert used == "individual" and len(rs) == 4


# ─────────────────────────────── API ──────────────────────────────────

def test_gravimetric_endpoint():
    client = TestClient(app)
    data = (FIXTURES / "gravimetric_template.csv").read_bytes()
    r = client.post("/gravimetric", files={"file": ("gravimetric_template.csv", data, "text/csv")})
    assert r.status_code == 200 and r.json()["summary"]["FAIL"] == 2
    bad = client.post("/gravimetric", files={"file": ("x.txt", b"nothing useful", "text/plain")})
    assert bad.status_code == 422
