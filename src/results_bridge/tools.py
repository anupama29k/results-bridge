"""Tool layer: the operations an AI assistant can perform on Results
Bridge, written as plain Python functions.

Design note: the MCP server in mcp_server.py is a thin wrapper that
registers THESE functions as tools. Keeping the logic here, SDK-free,
means the whole tool surface is unit-testable on any Python, and the
same functions could later back a CLI or a second protocol without
change. Tools never write to Benchling — checking a file records an
audit row but delivery stays out of scope for the assistant. An agent
gets read-and-evaluate powers, not write powers: least privilege.
"""
import os

from . import audit
from .parsers import parse_instrument_file
from .validation import load_criteria, validate_rows, summarize, plate_values, combine_overall
from .plate_scoring import score_plate


def list_assays() -> list:
    """Return every assay this service can validate against, sorted by id."""
    crit = load_criteria()
    return [
        {"assay_id": k, "description": v["description"],
         "regulatory": v.get("regulatory", []),
         "min": v["min"], "max": v["max"], "unit": v["unit"],
         "plate_criteria": [c["key"] for c in v.get("plate", {}).get("criteria", [])]}
        for k, v in sorted(crit.items())
    ]


def check_file(file_path: str, assay_id: str, plate_map_path: str = None) -> dict:
    """Parse an instrument file from disk, validate it against one assay,
    and record the check in the audit log. Never writes to Benchling.

    M7: an optional plate map (JSON file of {well: role}) makes validation
    role-aware — blanks judged as blanks, samples as samples — using the
    per-role rules in the assay's criteria entry.

    M8: when the assay has a "plate" block and the plate map gives standard
    concentrations, the plate is also scored as a whole (curve fit, blank,
    replicate CV, QC/spike recovery) and the result is returned under
    "plate". A plate that is scored and does not PASS makes the overall
    verdict ATTENTION_REQUIRED."""
    criteria = load_criteria()
    if assay_id not in criteria:
        raise ValueError(f"unknown assay_id '{assay_id}'; known: {sorted(criteria)}")
    plate_map = None
    if plate_map_path:
        import json
        plate_map = json.load(open(plate_map_path))
    name = os.path.basename(file_path)
    raw = open(file_path, "rb").read()
    content = raw.decode("utf-8") if name.lower().endswith((".csv", ".tsv", ".txt")) else raw
    fmt, rows = parse_instrument_file(name, content)
    crit = criteria[assay_id]
    results = validate_rows(rows, crit, plate_map)
    counts, overall = summarize(results)
    plate = None
    if plate_map is not None and "plate" in crit:
        plate = score_plate(plate_values(rows, crit), plate_map, crit["plate"])
    overall = combine_overall(overall, plate)
    audit.record(sha=audit.file_sha(raw), filename=name, fmt=fmt, assay=assay_id,
                 counts=counts, overall=overall, delivery="mcp-check (not delivered)",
                 plate_verdict=None if plate is None else plate["verdict"])
    out = {"file": name, "assay_id": assay_id, "format": fmt,
           "summary": counts, "overall": overall, "results": results}
    if plate is not None:
        out["plate"] = plate
    return out


def recent_audit(limit: int = 20) -> list:
    """Return the most recent audit rows, newest first."""
    return audit.recent(limit)


def search_protocols(query: str) -> list:
    """Search the robotic-assays protocol library by name (case-insensitive
    substring). Compact hits only — full protocols come from get_protocol.
    An empty result is an answer, not an error."""
    from .library import load_all_assays, display_name
    q = query.lower()
    return [
        {"name": display_name(e), "source": e["source"],
         "automation_difficulty": e.get("automation_difficulty"),
         "regulatory": e.get("regulatory", [])}
        for e in load_all_assays() if q in display_name(e).lower()
    ]


def get_protocol(name: str) -> dict:
    """Return the full library entry for one assay: every acceptance
    criterion (including the curve/ratio ones the validator can't score),
    troubleshooting, regulatory references, automation notes.

    Case-insensitive exact match on the display name. When several modules
    define the same assay, the LAST match wins — module order puts v2
    schemas after v1, so the newest schema prevails."""
    from .library import load_all_assays, display_name
    match = None
    for e in load_all_assays():
        if display_name(e).lower() == name.lower():
            match = e
    if match is None:
        raise ValueError(f"no protocol named '{name}'; try search_protocols first")
    return match


def check_gravimetric(file_path: str, temperature_C: float = None, liquid: str = None,
                      target_uL: float = None, mode: str = "auto", channel: str = "1") -> dict:
    """Gravimetric liquid-class verification (M9). Parse a balance file, convert
    each dispense mass to volume (ISO 8655-6 style, temperature- and density-
    corrected) and score %DEV / %CV per liquid x volume x channel against
    gravimetric.json. Records an audit row; never writes to Benchling.

    Template CSV files carry liquid and target in their columns. A raw balance
    print stream (Mettler SICS / Sartorius) does not, so pass `liquid` and
    `target_uL`, and optionally `mode` ("individual" tared readings or
    "cumulative" running total; "auto" decides and reports)."""
    from . import gravimetric as g
    name = os.path.basename(file_path)
    raw = open(file_path, "rb").read()
    text = raw.decode("utf-8-sig")
    cfg = g.load_config()
    meta, fmt = {}, None
    try:
        meta, readings = g.parse_template_csv(text)
        fmt = "gravimetric_template_csv"
    except ValueError as template_err:
        try:
            values = g.parse_balance_stream(text)
        except ValueError:
            raise ValueError(f"{name}: not a gravimetric template ({template_err}) and no "
                             f"balance readings recognised") from None
        if liquid is None or target_uL is None:
            raise ValueError(f"{name} is a raw balance stream: pass liquid and target_uL")
        used, readings = g.stream_to_readings(values, liquid, target_uL, mode, channel)
        fmt = f"balance_stream_{used}"
    result = g.score_gravimetric(readings, cfg, meta, temperature_C)
    audit.record(sha=audit.file_sha(raw), filename=name, fmt=fmt, assay="GRAVIMETRIC_liquid_class",
                 counts=result["summary"], overall=result["overall"],
                 delivery="mcp-check (not delivered)")
    return {"file": name, "format": fmt, "metadata": meta, **result}
