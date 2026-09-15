"""M5 tool layer: the three operations an AI assistant can perform on
Results Bridge, written as plain Python functions.

*** M5: implemented by Anu. *** The contracts are specified per function;
the bodies are hers to write — each one reuses code that already exists
(validation, parsers, audit), so this is composition, not invention.

Design note (interview-worthy): the MCP server in mcp_server.py is a thin
wrapper that registers THESE functions as tools. Keeping the logic here,
SDK-free, means (a) the whole tool surface is unit-testable on any Python,
and (b) the same functions could later back a CLI or a second protocol
without change. Tools never write to Benchling — checking a file records
an audit row but delivery stays out of scope for the assistant. An agent
gets read-and-evaluate powers, not write powers: least privilege.
"""
import os

from . import audit
from .parsers import parse_instrument_file
from .validation import load_criteria, validate_rows, summarize


def list_assays() -> list:
    """Return every assay this service can validate against.

    Contract:
      * load the criteria via load_criteria()
      * return a list of dicts, one per assay, sorted by assay_id:
          {"assay_id": <key>, "description": ..., "min": ..., "max": ...,
           "unit": ...}
        (values come straight from each criteria.json entry)
    """
    raise NotImplementedError("M5: Anu implements list_assays — see docstring")


def check_file(file_path: str, assay_id: str) -> dict:
    """Parse an instrument file from disk and validate it against one assay.

    Contract:
      * criteria = load_criteria(); if assay_id not in criteria, raise
        ValueError(f"unknown assay_id '{assay_id}'; known: {sorted(criteria)}")
      * name = os.path.basename(file_path)
      * read the file: if name (lowercased) ends with .csv/.tsv/.txt, read
        it as TEXT (open(file_path).read()); otherwise read BYTES
        (open(file_path, "rb").read())  — same rule /ingest uses
      * fmt, rows = parse_instrument_file(name, content)
      * results = validate_rows(rows, criteria[assay_id])
      * counts, overall = summarize(results)
      * audit it — checking is an event worth recording:
          audit.record(sha=audit.file_sha(raw_bytes), filename=name,
                       fmt=fmt, assay=assay_id, counts=counts,
                       overall=overall, delivery="mcp-check (not delivered)")
        (hint: you need the BYTES for the sha even when you parsed text —
        read the file once as bytes, and .decode("utf-8") for the text case)
      * return {"file": name, "assay_id": assay_id, "format": fmt,
                "summary": counts, "overall": overall, "results": results}
    """
    raise NotImplementedError("M5: Anu implements check_file — see docstring")


def recent_audit(limit: int = 20) -> list:
    """Return the most recent audit rows, newest first.

    Contract: delegate to audit.recent(limit) — this one is a one-liner,
    and that's the point: the audit module already knows how.
    """
    raise NotImplementedError("M5: Anu implements recent_audit — see docstring")
