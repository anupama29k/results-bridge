"""M6 contract tests: protocol knowledge served from the library.

Skip until search_protocols/get_protocol are implemented, then arm.
Loop:  ROBOTIC_ASSAYS_PATH=~/robotic-assays pytest tests/test_protocol_tools.py -v
"""
import pytest

from results_bridge import tools


def _implemented() -> bool:
    try:
        tools.search_protocols("elisa")
    except NotImplementedError:
        return False
    except Exception:
        return True
    return True


pytestmark = pytest.mark.skipif(
    not _implemented(),
    reason="M6 not implemented yet — write search_protocols/get_protocol in tools.py",
)


def test_search_is_case_insensitive_and_compact():
    hits = tools.search_protocols("ELISA")
    assert len(hits) >= 2  # sandwich ELISA exists in v1 and v2 at least
    for h in hits:
        assert set(h) == {"name", "source", "automation_difficulty", "regulatory"}
        assert "elisa" in h["name"].lower()


def test_search_no_match_is_empty_not_error():
    assert tools.search_protocols("xyzzy-not-an-assay") == []


def test_get_protocol_returns_full_entry():
    entry = tools.get_protocol("BCA protein concentration assay")
    assert "acceptance_criteria" in entry
    assert entry["source"] == "field_01_biopharma_v2"  # newest schema wins
    # the knowledge layer serves criteria the validator can't score:
    assert "standard_curve_r2" in entry["acceptance_criteria"]


def test_get_protocol_unknown_raises_with_guidance():
    with pytest.raises(ValueError) as exc:
        tools.get_protocol("Nonexistent Assay")
    assert "search_protocols" in str(exc.value)
