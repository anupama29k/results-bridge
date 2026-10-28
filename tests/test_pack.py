"""M10: validation-pack assembler, on the demonstration ELISA engagement."""
import copy
import json
import pathlib
import re
import shutil

import pytest

from results_bridge import pack, tools

DEMO = pathlib.Path(__file__).resolve().parents[1] / "demo" / "elisa"


@pytest.fixture
def engagement(tmp_path):
    """A writable copy of the demo engagement."""
    dst = tmp_path / "elisa"
    shutil.copytree(DEMO, dst, ignore=shutil.ignore_patterns("make_demo.py", "__pycache__"))
    return dst


def build(eng, tmp_path, mutate=None):
    m = json.loads((eng / "manifest.json").read_text())
    if mutate:
        mutate(m)
    (eng / "manifest.json").write_text(json.dumps(m))
    out = tmp_path / "pack.html"
    summary = tools.build_validation_pack(str(eng / "manifest.json"), str(out))
    return summary, out.read_text()


def test_demo_pack_is_accepted_from_three_consecutive_runs(engagement, tmp_path):
    summary, doc = build(engagement, tmp_path)
    assert summary["accepted"] is True
    assert summary["status"] == "DEMONSTRATION: criteria met on simulated data"
    assert summary["runs"] == [("PQ-1", "PASS"), ("PQ-2", "FAIL"), ("PQ-3", "PASS"),
                               ("PQ-4", "PASS"), ("PQ-5", "PASS")]
    assert all(v == "PASS" for *_, v in summary["liquid_classes"])


def test_every_section_and_the_watermark_are_present(engagement, tmp_path):
    _, doc = build(engagement, tmp_path)
    for i, title in enumerate(pack.SECTION_TITLES, start=1):
        assert f"{i}. {title}" in doc
    assert "class='watermark'" in doc and "SIMULATED DATA" in doc


def test_failed_run_and_planned_deviations_are_in_the_deviation_log(engagement, tmp_path):
    _, doc = build(engagement, tmp_path)
    section = doc.split("8. Deviations and observations")[1].split("9. Known limitations")[0]
    assert "intraplate_cv_max: FAIL" in section and "PQ-2" in section
    assert section.count("Planned deviation from library method") == 3


def test_traceability_hashes_match_the_files(engagement, tmp_path):
    _, doc = build(engagement, tmp_path)
    section = doc.split("11. Traceability")[1]
    for rel in ("protocol/bio_005_ot2.py", "runs/PQ-2_spark.xlsx", "gravimetric/liquid_classes.csv"):
        assert pack.sha256(str(engagement / rel)) in section


def test_status_is_computed_not_trusted(engagement, tmp_path):
    def drop_last_run(m):
        m["runs"] = m["runs"][:-1]       # PQ-3, PQ-4 only after the failure
    summary, _ = build(engagement, tmp_path, drop_last_run)
    assert summary["accepted"] is False
    assert any("2 consecutive PQ run(s) passed; 3 required" in r for r in summary["reasons"])


def test_unverified_liquid_class_blocks_acceptance(engagement, tmp_path):
    def require_more(m):
        m["liquid_classes_required"].append({"liquid": "DMSO", "target_uL": 20, "pipette": "p300_single_gen2"})
    summary, doc = build(engagement, tmp_path, require_more)
    assert summary["accepted"] is False
    assert any("DMSO" in r and "NOT VERIFIED" in r for r in summary["reasons"])


def test_simulation_failure_blocks_acceptance(engagement, tmp_path):
    def sim_failed(m):
        m["protocol"]["simulator_exit_code"] = 1
    summary, _ = build(engagement, tmp_path, sim_failed)
    assert "protocol did not complete simulation" in summary["reasons"]


def test_non_demonstration_pack_has_no_watermark(engagement, tmp_path):
    def real(m):
        m["demonstration"] = False
    summary, doc = build(engagement, tmp_path, real)
    assert summary["status"] == "ACCEPTED"
    assert "class='watermark'" not in doc


def test_protocol_header_is_parsed():
    p = pack.parse_protocol(str(DEMO / "protocol" / "bio_005_ot2.py"))
    assert p["requirements"]["robotType"] == "OT-2"
    assert any("slot  1" in d or "slot 1" in d for d in p["sections"]["deck"])
    assert len(p["sections"]["reconciliation"]) == 3
    assert any("PBST" in f for f in p["sections"]["fill"])


def test_pdf_output(engagement, tmp_path):
    pytest.importorskip("weasyprint")
    out_pdf = tmp_path / "pack.pdf"
    summary = tools.build_validation_pack(str(engagement / "manifest.json"), str(tmp_path / "p.html"), str(out_pdf))
    data = out_pdf.read_bytes()
    assert summary["pdf"] and data[:4] == b"%PDF" and len(data) > 20000
