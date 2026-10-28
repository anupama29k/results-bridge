# Results Bridge  
![CI](https://github.com/anupama29k/results-bridge/actions/workflows/ci.yml/badge.svg)
**Live service:** [results-bridge.onrender.com/docs](https://results-bridge.onrender.com/docs) — interactive API docs; try `/ingest` with a Qubit CSV. Runs in dry-run mode (no credentials): full parse → validate → audit pipeline, with the Benchling payload rendered instead of sent. Free-tier host, so the first request after idle takes ~30s to wake.

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

## Plate-level scoring

Per-well ranges say whether a reading is plausible. Whether a **plate** is reportable depends on criteria that need every well at once. Give `check_file` (or `/ingest`, field `plate_map`) a plate map with standard concentrations and Results Bridge also:

- fits the standard curve (`linear`, `quadratic` or `4pl`), after blank subtraction where configured
- scores the assay's plate criteria: curve R², blank background, 4PL asymptotes, replicate CV, QC recovery, spike recovery
- back-calculates every sample (× dilution) and flags samples outside the standard range or above a saturation threshold

```json
{"_meta": {"conc_unit": "ug/mL"},
 "A1": {"role": "standard", "conc": 2000, "group": "STD1"},
 "H1": {"role": "blank", "conc": 0},
 "A3": {"role": "sample", "group": "S01", "dilution": 1},
 "H11": {"role": "qc", "group": "QC1", "expected": 618.2},
 "G11": {"role": "positive_control", "group": "P01", "base_group": "S38", "spike": 0.3}}
```

The robotic-assays Opentrons code generator writes these maps for every protocol it emits (`protocols/opentrons/plate_maps/`). The criteria live in the assay's `plate` block in `criteria.json`; each `key` is the library's own acceptance-criterion key, and a test keeps them in step.

A plate verdict is **PASS**, **FAIL**, **INVALID** (could not be computed, e.g. the fit failed) or **INCOMPLETE** (a configured criterion had nothing to score, e.g. no QC wells). INCOMPLETE is never reported as a pass: a scored plate that is not PASS turns the overall verdict to `ATTENTION_REQUIRED`. Role-only (M7) maps still work exactly as before; their plate result is `NOT_EVALUATED`.

## Gravimetric liquid-class verification

The other half of a validation pack: does the robot deliver the volume it is told to, for each real liquid? `check_gravimetric` (MCP) and `POST /gravimetric` take balance data and return, per liquid × target volume × channel, mean volume, **%DEV** (systematic error), **%CV** (random error) and PASS / FAIL / INVALID against `gravimetric.json`.

- Mass → volume the ISO 8655-6 way: `V = m / (ρL − ρair) × (1 − ρair / ρweights)`, water density from temperature (Tanaka 2001); tests check the result against ISO 8655-6 Z-factors (1.0029 µL/mg at 20 °C).
- Non-water liquids use their density from `gravimetric.json` or a `# density_<liquid>:` file header. A liquid with no known density is **INVALID** — never silently converted as water.
- Inputs: the gravimetric template CSV (`liquid, target_uL, channel, replicate, mass_mg`, `#` metadata lines; LabX-style `weight, unit` columns also map) or a raw **Mettler SICS / Sartorius** print stream, tared per dispense or cumulative on one vessel (auto-detected and reported).
- Fewer than 10 dispenses, or no temperature, is INVALID. Dispenses > 3 SD from the mean are flagged for investigation, not removed.
- Limits are project defaults by volume band (≤ 20 µL: 5 % / 5 %; ≤ 100 µL: 3 % / 3 %; ≤ 1000 µL: 2 % / 2 %) with per-liquid overrides. Agree them with the client before verification.

```bash
curl -F "file=@tests/fixtures/gravimetric_template.csv" localhost:8080/gravimetric
curl -F "file=@tests/fixtures/balance_sics_cumulative.txt" -F liquid=water -F target_uL=100 \
     -F temperature_C=21.0 localhost:8080/gravimetric
```

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
| `src/results_bridge/gravimetric.py` + `gravimetric.json` | Balance data → liquid-class %DEV / %CV table |
| `src/results_bridge/plate_scoring.py` | Plate-level scoring: curve fit, blank, CV, QC / spike recovery, back-calculation |
| `src/results_bridge/benchling.py` | Delivery layer: dry-run client + live client (auth, idempotency, retries) |
| `src/results_bridge/audit.py` | Append-only SQLite audit log |
| `tests/` | pytest suite against real instrument sample files; runs in CI on every push |

## Author

Designed and built by **Anupama Kozhiyalam** · [github.com/anupama29k](https://github.com/anupama29k) · MIT License
