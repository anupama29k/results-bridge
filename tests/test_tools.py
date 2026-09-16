"""M5 contract tests: the tool layer an AI assistant will call.

Same pattern as the M3 harness: these SKIP until the functions in
src/results_bridge/tools.py stop raising NotImplementedError, then arm
automatically. Implement, then run:

    ROBOTIC_ASSAYS_PATH=~/robotic-assays pytest tests/test_tools.py -v
"""
import pathlib

import pytest

from results_bridge import tools

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _implemented() -> bool:
    try:
        tools.list_assays()
    except NotImplementedError:
        return False
    except Exception:
        return True
    return True


pytestmark = pytest.mark.skipif(
    not _implemented(),
    reason="M5 not implemented yet — write the functions in tools.py, these tests arm automatically",
)


def test_list_assays_shape():
    assays = tools.list_assays()
    assert len(assays) >= 4
    ids = [a["assay_id"] for a in assays]
    assert ids == sorted(ids)  # contract says sorted
    ngs = next(a for a in assays if a["assay_id"] == "NGS_001_library_prep_input")
    assert ngs["min"] == 10.0 and ngs["max"] == 100.0 and ngs["unit"] == "ng/uL"
    assert set(ngs) == {"assay_id", "description", "regulatory", "min", "max", "unit"}


def test_check_file_qubit_end_to_end():
    out = tools.check_file(str(FIXTURES / "test_qubit.csv"), "NGS_001_library_prep_input")
    assert out["summary"] == {"PASS": 2, "FAIL": 2, "INVALID": 1}
    assert out["overall"] == "ATTENTION_REQUIRED"
    assert out["file"] == "test_qubit.csv"
    assert len(out["results"]) == 5  # nothing silently dropped
    # checking a file is an audited event
    rows = tools.recent_audit(5)
    assert rows and rows[0]["filename"] == "test_qubit.csv"
    assert "mcp" in rows[0]["delivery"]


def test_check_file_excel_path():
    out = tools.check_file(str(FIXTURES / "test_tecan_spark.xlsx"), "ELISA_001_tmb_endpoint")
    counts = out["summary"]
    assert counts["PASS"] + counts["FAIL"] + counts["INVALID"] == 96


def test_check_file_unknown_assay_raises():
    with pytest.raises(ValueError) as exc:
        tools.check_file(str(FIXTURES / "test_qubit.csv"), "NOT_AN_ASSAY")
    assert "unknown assay_id" in str(exc.value)


def test_recent_audit_respects_limit():
    tools.check_file(str(FIXTURES / "test_qubit.csv"), "NGS_001_library_prep_input")
    tools.check_file(str(FIXTURES / "test_qubit.csv"), "NGS_001_library_prep_input")
    assert len(tools.recent_audit(1)) == 1
