"""Acceptance-criteria validation.

Criteria live in criteria.json at the repo root — that file is Anu's to own
and extend (M2): keys are assay ids; each entry names the measurement path,
allowed range and unit. The engine below applies them uniformly and treats
missing values as INVALID — never silently dropped.
"""
import json, os
from typing import Any

_CRITERIA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "criteria.json")

def load_criteria() -> dict:
    with open(_CRITERIA_PATH) as f:
        return json.load(f)

def _dig(row: dict, path: list[str]) -> Any:
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur

def validate_rows(rows: list[dict], crit: dict) -> list[dict]:
    out = []
    for r in rows:
        value = _dig(r, crit["param_path"])
        entry = {"sample_id": r.get("sample_id"), "value": value, "unit": crit["unit"]}
        if value is None:
            entry |= {"verdict": "INVALID",
                      "reason": "no numeric value reported by instrument"}
        elif value < crit["min"]:
            entry |= {"verdict": "FAIL",
                      "reason": f"{value} {crit['unit']} < minimum {crit['min']}"}
        elif value > crit["max"]:
            entry |= {"verdict": "FAIL",
                      "reason": f"{value} {crit['unit']} > maximum {crit['max']}"}
        else:
            entry |= {"verdict": "PASS", "reason": None}
        out.append(entry)
    return out

def summarize(results: list[dict]) -> tuple[dict, str]:
    counts = {"PASS": 0, "FAIL": 0, "INVALID": 0}
    for r in results:
        counts[r["verdict"]] += 1
    overall = "ALL_PASS" if counts["FAIL"] == 0 and counts["INVALID"] == 0 else "ATTENTION_REQUIRED"
    return counts, overall
