"""Results Bridge — instrument output → validated → Benchling.

POST /ingest   multipart: file + assay_id  → parse, validate, deliver, audit
GET  /audit    recent ingestions (traceability)
GET  /healthz  liveness
"""
import logging
from fastapi import FastAPI, UploadFile, Form, HTTPException

from .parsers import parse_instrument_file
from .validation import load_criteria, validate_rows, summarize
from .benchling import build_payload, get_client
from . import audit

log = logging.getLogger("results_bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Results Bridge",
              description="Instrument results, validated against assay acceptance criteria, delivered to Benchling.")


@app.post("/ingest")
async def ingest(file: UploadFile, assay_id: str = Form(...)):
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

    results = validate_rows(rows, criteria[assay_id])
    counts, overall = summarize(results)
    client = get_client()
    delivery = client.post_results(build_payload(assay_id, results, sha))
    audit.record(sha=sha, filename=file.filename, fmt=fmt, assay=assay_id,
                 counts=counts, overall=overall, delivery=client.mode)
    log.info("ingested file=%s sha=%s assay=%s overall=%s %s", file.filename, sha, assay_id, overall, counts)
    return {"assay": assay_id, "file_sha256": sha, "format": fmt,
            "overall": overall, "summary": counts, "results": results,
            "delivery": delivery}


@app.get("/audit")
def get_audit(limit: int = 50):
    return audit.recent(limit)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": get_client().mode}
