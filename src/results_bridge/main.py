"""Results Bridge — instrument output → validated → Benchling.

POST /ingest   multipart: file + assay_id  → parse, validate, deliver, audit
POST /gravimetric  balance file -> liquid-class %DEV / %CV table
GET  /audit    recent ingestions (traceability)
GET  /healthz  liveness
"""
import json
import logging
from typing import Optional

from fastapi import FastAPI, UploadFile, Form, HTTPException

from .parsers import parse_instrument_file
from .validation import load_criteria, validate_rows, summarize, plate_values, combine_overall
from .plate_scoring import score_plate
from .benchling import build_payload, get_client
from . import audit

log = logging.getLogger("results_bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Results Bridge",
              description="Instrument results, validated against assay acceptance criteria, delivered to Benchling.")


@app.post("/ingest")
async def ingest(file: UploadFile, assay_id: str = Form(...), plate_map: Optional[str] = Form(None)):
    criteria = load_criteria()
    if assay_id not in criteria:
        raise HTTPException(422, f"unknown assay_id '{assay_id}'; known: {sorted(criteria)}")
    raw = await file.read()
    sha = audit.file_sha(raw)
    try:
        content = raw.decode("utf-8", errors="strict") if file.filename.lower().endswith((".csv", ".tsv", ".txt")) else raw
        fmt, rows = parse_instrument_file(file.filename, content)
    except Exception as e:
        log.warning("parse failed file=%s sha=%s err=%s", file.filename, sha, e)
        raise HTTPException(422, f"could not parse {file.filename}: {e}")

    pm = None
    if plate_map:
        try:
            pm = json.loads(plate_map)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"plate_map is not valid JSON: {e}")
    crit = criteria[assay_id]
    try:
        results = validate_rows(rows, crit, pm)
        plate = score_plate(plate_values(rows, crit), pm, crit["plate"]) if pm is not None and "plate" in crit else None
    except ValueError as e:
        raise HTTPException(422, f"plate map rejected: {e}")
    counts, overall = summarize(results)
    overall = combine_overall(overall, plate)
    client = get_client()
    delivery = client.post_results(build_payload(assay_id, results, sha))
    audit.record(sha=sha, filename=file.filename, fmt=fmt, assay=assay_id,
                 counts=counts, overall=overall, delivery=client.mode,
                 plate_verdict=None if plate is None else plate["verdict"])
    log.info("ingested file=%s sha=%s assay=%s overall=%s %s", file.filename, sha, assay_id, overall, counts)
    return {"assay": assay_id, "file_sha256": sha, "format": fmt,
            "overall": overall, "summary": counts, "results": results,
            "plate": plate, "delivery": delivery}


@app.post("/gravimetric")
async def gravimetric(file: UploadFile, temperature_C: Optional[float] = Form(None),
                      liquid: Optional[str] = Form(None), target_uL: Optional[float] = Form(None),
                      mode: str = Form("auto"), channel: str = Form("1")):
    """Gravimetric liquid-class verification (template CSV or balance print stream)."""
    import os, tempfile
    from . import tools
    raw = await file.read()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, os.path.basename(file.filename or "balance.csv"))
        with open(path, "wb") as fh:
            fh.write(raw)
        try:
            return tools.check_gravimetric(path, temperature_C, liquid, target_uL, mode, channel)
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.get("/audit")
def get_audit(limit: int = 50):
    return audit.recent(limit)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": get_client().mode}
