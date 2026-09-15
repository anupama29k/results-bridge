"""Eval harness for the Results Bridge tool layer (M5).

Philosophy: an eval is the spec. Each case in cases.json states what the
tools MUST return for a known input; the runner calls the real tool layer
and diffs. Deterministic graders (exact verdict counts) do all the work —
no LLM judging needed, because this layer's correctness is checkable.

Run:  ROBOTIC_ASSAYS_PATH=~/robotic-assays python evals/run_evals.py
Add cases (Anu's job): new entries in cases.json — especially edge cases:
an empty file, a file for the wrong instrument, an all-PASS plate, an
assay/file mismatch. The cases that try to break it are worth the most.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from results_bridge import tools  # noqa: E402


def run() -> int:
    cases = json.loads((ROOT / "evals" / "cases.json").read_text())
    failures = 0
    print(f"{'CASE':42} {'RESULT':8} DETAIL")
    print("-" * 78)
    for case in cases:
        try:
            out = tools.check_file(str(ROOT / case["file"]), case["assay_id"])
            got = {"summary": out["summary"], "overall": out["overall"]}
            if got == case["expect"]:
                print(f"{case['name']:42} {'PASS':8} {got['summary']}")
            else:
                failures += 1
                print(f"{case['name']:42} {'FAIL':8} expected {case['expect']} got {got}")
        except Exception as e:  # an eval crash is a failing eval, loudly
            failures += 1
            print(f"{case['name']:42} {'ERROR':8} {type(e).__name__}: {e}")
    print("-" * 78)
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
