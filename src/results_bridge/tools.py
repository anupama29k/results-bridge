"""M5 tool layer: the three operations an AI assistant can perform on
Results Bridge, written as plain Python functions.

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
from .validation import load_criteria, validate_rows, summarize


def list_assays() -> list:
    """Return every assay this service can validate against, sorted by id."""
    crit = load_criteria()
    return [
        {"assay_id": k, "description": v["description"],
         "regulatory": v.get("regulatory", []),
         "min": v["min"], "max": v["max"], "unit": v["unit"]}
        for k, v in sorted(crit.items())
    ]


def check_file(file_path: str, assay_id: str) -> dict:
    """Parse an instrument file from disk, validate it against one assay,
    and record the check in the audit log. Never writes to Benchling."""
    criteria = load_criteria()
    if assay_id not in criteria:
        raise ValueError(f"unknown assay_id '{assay_id}'; known: {sorted(criteria)}")
    name = os.path.basename(file_path)
    raw = open(file_path, "rb").read()
    content = raw.decode("utf-8") if name.lower().endswith((".csv", ".tsv", ".txt")) else raw
    fmt, rows = parse_instrument_file(name, content)
    results = validate_rows(rows, criteria[assay_id])
    counts, overall = summarize(results)
    audit.record(sha=audit.file_sha(raw), filename=name, fmt=fmt, assay=assay_id,
                 counts=counts, overall=overall, delivery="mcp-check (not delivered)")
    return {"file": name, "assay_id": assay_id, "format": fmt,
            "summary": counts, "overall": overall, "results": results}


def recent_audit(limit: int = 20) -> list:
    """Return the most recent audit rows, newest first."""
    return audit.recent(limit)

def search_protocols(query: str) -> list:
    """Search the robotic-assays protocol library by name.

    *** M6 (Anu): implement. ***
    Contract:
      * from .library import load_all_assays, display_name
      * case-insensitive substring match of `query` against each entry's
        display_name(entry)
      * return compact hits (full protocols come from get_protocol):
          {"name": display_name(e), "source": e["source"],
           "automation_difficulty": e.get("automation_difficulty"),
           "regulatory": e.get("regulatory", [])}
      * empty list when nothing matches — an empty search is an answer,
        not an error
    """
    raise NotImplementedError("M6: Anu implements search_protocols — see docstring")


def get_protocol(name: str) -> dict:
    """Return the full library entry for one assay: every acceptance
    criterion (including the curve/ratio ones the validator can't score),
    troubleshooting, regulatory references, automation notes.

    *** M6 (Anu): implement. ***
    Contract:
      * case-insensitive EXACT match on display_name(entry)
      * if several modules define the same name, return the LAST match
        (module order puts v2 schemas after v1 — newest schema wins)
      * no match: raise ValueError(f"no protocol named '{name}'; try
        search_protocols first") — steer the caller to discovery
      * return the entry dict as-is (it already carries `source`)
    """
    raise NotImplementedError("M6: Anu implements get_protocol — see docstring")
