from fastapi.testclient import TestClient
from results_bridge.main import app

client = TestClient(app)

def test_healthz_reports_dry_run():
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["mode"] == "dry-run"

def test_ingest_end_to_end(qubit_csv):
    r = client.post("/ingest", data={"assay_id": "NGS_001_library_prep_input"},
                    files={"file": ("test_qubit.csv", qubit_csv, "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] == "ATTENTION_REQUIRED"
    assert body["summary"] == {"PASS": 2, "FAIL": 2, "INVALID": 0}
    assert body["delivery"]["mode"] == "dry-run"
    a = client.get("/audit").json()
    assert a and a[0]["overall"] == "ATTENTION_REQUIRED" and a[0]["n_fail"] == 2

def test_unknown_assay_rejected(qubit_csv):
    r = client.post("/ingest", data={"assay_id": "nope"},
                    files={"file": ("test_qubit.csv", qubit_csv, "text/csv")})
    assert r.status_code == 422
