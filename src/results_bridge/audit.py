"""Append-only SQLite audit log: every ingestion is traceable."""
import sqlite3, datetime, hashlib, threading
from .config import AUDIT_DB_PATH

_lock = threading.Lock()

def _conn():
    c = sqlite3.connect(AUDIT_DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS audit(
        ts TEXT, file_sha256 TEXT, filename TEXT, fmt TEXT, assay TEXT,
        n_samples INT, n_pass INT, n_fail INT, n_invalid INT,
        overall TEXT, delivery TEXT)""")
    return c

def file_sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def record(*, sha, filename, fmt, assay, counts, overall, delivery):
    with _lock:
        c = _conn()
        c.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (datetime.datetime.now(datetime.timezone.utc).isoformat(), sha, filename, fmt, assay,
                   counts["PASS"] + counts["FAIL"] + counts["INVALID"],
                   counts["PASS"], counts["FAIL"], counts["INVALID"], overall, delivery))
        c.commit(); c.close()

def recent(limit: int = 50) -> list[dict]:
    cols = ["ts","file_sha256","filename","fmt","assay","n_samples",
            "n_pass","n_fail","n_invalid","overall","delivery"]
    with _lock:
        c = _conn()
        rows = c.execute(f"SELECT * FROM audit ORDER BY ts DESC LIMIT {int(limit)}").fetchall()
        c.close()
    return [dict(zip(cols, r)) for r in rows]
