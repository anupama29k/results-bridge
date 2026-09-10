# Results Bridge

**Instrument output in → validated against assay acceptance criteria → structured result in Benchling. Built, deployed, and operated as a small production service.**

```
instrument file ──► POST /ingest ──► parser (robotic-assays) ──► validation ──► Benchling /results
                                          │                          │               (or dry-run)
                                          └──────────► append-only audit log ◄───────┘
```

Results Bridge is the operations layer of a three-repo ecosystem: [robotic-assays](https://github.com/anupama29k/robotic-assays) is the hub (assay library, acceptance criteria, instrument parsers); this service consumes it to turn raw instrument files into validated, traceable ELN results.

## What one request does

`POST /ingest` with a Qubit/TapeStation/Tecan Spark/CLARIOstar file and an `assay_id`:

1. **Parse** — the file is parsed by the robotic-assays ingestion module (format auto-detected).
2. **Validate** — every sample is judged against the assay's acceptance criteria from `criteria.json`; missing values surface as `INVALID`, never silently dropped.
3. **Deliver** — a Benchling-shaped results payload is POSTed to the tenant's API, or returned verbatim in **dry-run mode** when no credentials are configured, so anyone can run the service keyless.
4. **Audit** — an append-only SQLite row records the file hash, assay, verdict counts and delivery mode: every decision traceable to its source file.

## Run it (keyless, 60 seconds)

```bash
git clone https://github.com/anupama29k/results-bridge.git
git clone https://github.com/anupama29k/robotic-assays.git   # side by side
cd results-bridge && pip install -r requirements.txt
PYTHONPATH=src uvicorn results_bridge.main:app --port 8080
curl -F "file=@tests/fixtures/test_qubit.csv" -F "assay_id=NGS_001_library_prep_input" localhost:8080/ingest
```

Live mode: set `BENCHLING_API_URL` and `BENCHLING_API_KEY` in the environment (never committed) and the same request writes real results with file-hash idempotency.

## Layout

| Path | What it is |
|---|---|
| `src/results_bridge/main.py` | FastAPI service: `/ingest`, `/audit`, `/healthz` |
| `src/results_bridge/parsers.py` | Adapter over the robotic-assays ingestion module |
| `src/results_bridge/validation.py` + `criteria.json` | Acceptance-criteria engine and the per-assay rules |
| `src/results_bridge/benchling.py` | Delivery layer: dry-run client + live client (auth, idempotency, retries) |
| `src/results_bridge/audit.py` | Append-only SQLite audit log |
| `tests/` | pytest suite against real instrument sample files; runs in CI on every push |

## Author

Designed and built by **Anupama Kozhiyalam** · [github.com/anupama29k](https://github.com/anupama29k) · MIT License
