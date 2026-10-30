"""Build the demonstration ELISA engagement (all data SIMULATED).

    ROBOTIC_ASSAYS_PATH=../robotic-assays \
    OPENTRONS_SIMULATE=.venv-sim/bin/opentrons_simulate \
    python demo/elisa/make_demo.py

Writes into demo/elisa/:
  protocol/bio_005_ot2.py     snapshot of the library-generated protocol
  protocol/simulate.log       opentrons_simulate output (kept if no simulator)
  runs/PQ-n_spark.xlsx        five simulated Tecan Spark reads
  runs/plate_map.json         the library plate map, with QC and spike wells
  gravimetric/liquid_classes.csv  per-liquid, per-pipette, per-channel masses
  manifest.json               the pack manifest

Scenario: PQ-1 passes; PQ-2 has a pipetting fault in column 7 (one replicate
of each sample in that column reads ~30% high) and fails replicate CV;
PQ-3..5 pass. The pack must report the PQ-2 deviation and still compute
acceptance from three consecutive passing runs.
"""
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
from make_plate_fixtures import layout, wells_of, write_spark  # noqa: E402
from results_bridge.gravimetric import water_density, z_factor  # noqa: E402

DENS = {"PBST": 1.005, "blocking_buffer": 1.008, "HRP_conjugate": 1.008, "TMB": 1.00,
        "stop_solution_2N_H2SO4": 1.061, "serum_sample": 1.024}
T_C = 21.4


def elisa_plate(seed, fault_col=None):
    rng = random.Random(seed)
    a, b, c, d = 0.05, 1.2, 0.10, 2.8
    sig = lambda x: d + (a - d) / (1 + (x / c) ** b) if x > 0 else a
    pm = layout(1.0, "x top standard", 3)
    true = {f"S{i:02d}": random.Random(100 + i).uniform(0.03, 1.2) for i in range(1, 41)}
    true["S39"] = true["S38"] + 0.30
    for w in wells_of(pm, "S39"):
        pm[w] = {"role": "positive_control", "group": "P01", "base_group": "S38", "spike": 0.30, "dilution": 3}
    for w in wells_of(pm, "S40"):
        pm[w] = {"role": "qc", "group": "QC1", "expected": round(true["S40"], 4), "dilution": 3}
    vals = {}
    for w, e in pm.items():
        if w.startswith("_"):
            continue
        if e["role"] in ("standard", "blank"):
            x = e.get("conc", 0)
        else:
            src = {"qc": "S40", "positive_control": "S39"}.get(e["role"], e["group"])
            x = true[src] / 3
        v = max(sig(x) * rng.gauss(1, 0.025), 0.001)   # 2.5% well CV: a well-tuned automated ELISA
        if fault_col and int(w[1:]) == fault_col:
            v *= 1.30
        vals[w] = v
    return pm, vals


def exact(rng, n, mean, cv):
    raw = [rng.gauss(0, 1) for _ in range(n)]
    m = sum(raw) / n
    s = (sum((x - m) ** 2 for x in raw) / (n - 1)) ** 0.5
    return [mean + (x - m) / s * mean * cv / 100 for x in raw]


def gravimetric_csv(path):
    rng = random.Random(86556)
    plan = [  # liquid, target, pipette, channels, DEV %, CV %
        ("PBST", 300, "p300_multi_gen2", 8, -0.6, 0.5),
        ("blocking_buffer", 200, "p300_multi_gen2", 8, -0.9, 0.7),
        ("blocking_buffer", 100, "p300_multi_gen2", 8, -1.1, 1.0),
        ("HRP_conjugate", 100, "p300_multi_gen2", 8, -0.8, 1.1),
        ("TMB", 100, "p300_multi_gen2", 8, -0.4, 1.2),
        ("stop_solution_2N_H2SO4", 100, "p300_multi_gen2", 8, -1.6, 1.0),
        ("blocking_buffer", 250, "p300_single_gen2", 1, -0.7, 0.5),
        ("serum_sample", 100, "p300_single_gen2", 1, -1.9, 1.3),
    ]
    lines = ["# SIMULATED DATA -- demonstration only",
             "# instrument: Opentrons OT-2 (SIM-0001), p300_single_gen2 left, p300_multi_gen2 right",
             "# balance: Mettler-Toledo XPR206 (0.01 mg), simulated",
             f"# temperature_C: {T_C}",
             "# density_serum_sample: 1.024",
             "liquid,target_uL,pipette,channel,replicate,mass_mg"]
    for liquid, target, pip, chans, dev, cv in plan:
        z = z_factor(DENS[liquid])
        for ch in range(1, chans + 1):
            ch_dev = dev + rng.uniform(-0.3, 0.3)
            for i, v in enumerate(exact(rng, 10, target * (1 + ch_dev / 100), cv), start=1):
                lines.append(f"{liquid},{target},{pip},{ch},{i},{v / z:.4f}")
    path.write_text("\n".join(lines) + "\n")
    return [{"liquid": l, "target_uL": t, "pipette": p} for l, t, p, *_ in plan]


def main():
    lib = os.environ.get("ROBOTIC_ASSAYS_PATH") or str(REPO.parent / "robotic-assays")
    src = pathlib.Path(lib) / "protocols" / "opentrons" / "bio_005_ot2.py"
    (HERE / "protocol").mkdir(exist_ok=True)
    (HERE / "runs").mkdir(exist_ok=True)
    (HERE / "gravimetric").mkdir(exist_ok=True)
    proto = HERE / "protocol" / "bio_005_ot2.py"
    if src.exists():
        shutil.copy(src, proto)
    elif not proto.exists():
        raise SystemExit(f"protocol not found at {src}; set ROBOTIC_ASSAYS_PATH")

    sim_cmd = os.environ.get("OPENTRONS_SIMULATE")
    log = HERE / "protocol" / "simulate.log"
    exit_code = json.loads((HERE / "manifest.json").read_text())["protocol"]["simulator_exit_code"] \
        if (HERE / "manifest.json").exists() else None
    if sim_cmd:
        res = subprocess.run([sim_cmd, str(proto)], capture_output=True, text=True)
        log.write_text(res.stdout + res.stderr)
        exit_code = res.returncode
    elif not log.exists():
        raise SystemExit("no simulator log; set OPENTRONS_SIMULATE (opentrons==8.8.2)")

    runs = []
    for n, (seed, fault) in enumerate([(11, None), (12, 7), (13, None), (14, None), (15, None)], start=1):
        pm, vals = elisa_plate(seed, fault)
        write_spark(vals, HERE / "runs" / f"PQ-{n}_spark.xlsx", 450)
        runs.append({"run_id": f"PQ-{n}", "date": f"2026-11-{2 + n:02d}", "operator": "A. Analyst (simulated)",
                     "file": f"runs/PQ-{n}_spark.xlsx", "plate_map": "runs/plate_map.json",
                     "reagent_lots": {"capture Ab": "CAP-2611", "HRP": "HRP-0914", "TMB": f"TMB-77{n}"}})
    (HERE / "runs" / "plate_map.json").write_text(json.dumps(pm, indent=2) + "\n")
    required = gravimetric_csv(HERE / "gravimetric" / "liquid_classes.csv")

    manifest = {
        "pack_id": "VP-DEMO-ELISA-001",
        "title": "Sandwich ELISA on Opentrons OT-2 -- demonstration validation pack",
        "demonstration": True,
        "client": {"name": "Example Biologics (fictional)", "site": "Bench 4, analytical development"},
        "robot": "Opentrons OT-2 (simulated, serial SIM-0001)",
        "prepared_by": "Implementer (demonstration)",
        "assay": {"library_assay_id": "BIO_005", "results_bridge_assay_id": "ELISA_001_tmb_endpoint"},
        "system": {"Robot": "Opentrons OT-2, serial SIM-0001 (simulated)",
                   "Left pipette": "p300_single_gen2", "Right pipette": "p300_multi_gen2",
                   "Plate reader": "Tecan Spark, 450 nm with 620 nm reference (simulated)",
                   "Balance": "Mettler-Toledo XPR206, 0.01 mg (simulated)"},
        "protocol": {"path": "protocol/bio_005_ot2.py", "simulator_log": "protocol/simulate.log",
                     "simulator": "opentrons_simulate (opentrons 8.8.2)", "simulator_exit_code": exit_code},
        "liquid_classes_required": required,
        "gravimetric": [{"path": "gravimetric/liquid_classes.csv", "date": "2026-11-02",
                         "operator": "A. Analyst (simulated)"}],
        "runs": runs,
        "required_consecutive_passes": 3,
        "training": [{"name": "A. Analyst (simulated)", "topics": "deck loading, fill sheet, pauses, "
                      "export upload, deviation handling", "solo_run": "PQ-5", "date": "2026-11-07"}],
        "limitations": ["Standard concentrations are relative to the top standard; the absolute value is set "
                        "per reference-standard lot."],
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"demo written to {HERE} (simulator exit {exit_code})")


if __name__ == "__main__":
    main()
