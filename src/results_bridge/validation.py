"""Acceptance-criteria validation.

Criteria live in criteria.json — Anu's to own and extend: keys are assay
ids; each entry names the measurement path, allowed range and unit. The
engine applies them uniformly and treats missing values as INVALID —
never silently dropped.

M7 — plate-map-aware validation: an assay entry may carry a "roles"
mapping with per-role rules, and validate_rows accepts an optional
plate_map ({well: role}). A well's role decides which rule judges it —
a blank SHOULD read low; the same 0.06 AU that fails a sample passes a
blank. Rules may be one-sided (min only, or max only), and a rule of
{"skip": true} excludes those wells from validation entirely. Without a
plate map, behavior is exactly as before: every row judged by the
assay's default min/max.
"""
import json, os
from typing import Any

_CRITERIA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "criteria.json")

def load_criteria() -> dict:
    with open(_CRITERIA_PATH) as f:
        return json.load(f)

def _dig(row: dict, path: list) -> Any:
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur

def validate_rows(rows: list, crit: dict, plate_map: dict = None) -> list:
    roles = crit.get("roles", {})
    out = []
    for r in rows:
        well = r.get("well_position") or r.get("sample_id")
        role = (plate_map or {}).get(well, "sample")
        rule = roles.get(role) if plate_map is not None else None
        if rule is None:
            rule = {"min": crit.get("min"), "max": crit.get("max")}
        if rule.get("skip"):
            continue
        value = _dig(r, crit["param_path"])
        entry = {"sample_id": r.get("sample_id"), "value": value,
                 "unit": crit["unit"],
                 "role": role if plate_map is not None else None}
        lo, hi = rule.get("min"), rule.get("max")
        if value is None:
            entry |= {"verdict": "INVALID",
                      "reason": "no numeric value reported by instrument"}
        elif lo is not None and value < lo:
            entry |= {"verdict": "FAIL",
                      "reason": f"{value} {crit['unit']} < minimum {lo}"
                               + (f" for role '{role}'" if plate_map is not None else "")}
        elif hi is not None and value > hi:
            entry |= {"verdict": "FAIL",
                      "reason": f"{value} {crit['unit']} > maximum {hi}"
                               + (f" for role '{role}'" if plate_map is not None else "")}
        else:
            entry |= {"verdict": "PASS", "reason": None}
        out.append(entry)
    return out

def summarize(results: list) -> tuple:
    counts = {"PASS": 0, "FAIL": 0, "INVALID": 0}
    for r in results:
        counts[r["verdict"]] += 1
    overall = "ALL_PASS" if counts["FAIL"] == 0 and counts["INVALID"] == 0 else "ATTENTION_REQUIRED"
    return counts, overall
