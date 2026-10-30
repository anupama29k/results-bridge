"""Generate the M9 gravimetric fixtures (deterministic; re-run to rebuild).

    python tests/fixtures/make_gravimetric_fixtures.py

Simulates an OT-2 P300 single-channel verification at 21.0 C: masses are the
volume actually delivered divided by the Z factor, so the expected verdicts are
known by construction:

    water 20 uL   DEV -1.0%  CV 1.5%   PASS   (limit 5 / 5)
    water 50 uL   DEV +0.8%  CV 1.0%   PASS   (limit 3 / 3)
    water 200 uL  DEV -2.6%  CV 0.6%   FAIL   (limit 2 / 2) -- needs liquid-class tuning
    DMSO 20 uL    DEV -6.0%  CV 2.5%   FAIL   (limit 5 / 5) -- classic DMSO under-delivery
    BCA WR 200 uL DEV -0.9%  CV 0.8%   PASS
    serum 50 uL   no density configured -> INVALID (never converted as water)
"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from results_bridge.gravimetric import water_density, z_factor  # noqa: E402

HERE = pathlib.Path(__file__).parent
T = 21.0
DENS = {"water": water_density(T), "DMSO": 1.100, "BCA_working_reagent": 1.03, "serum": 1.025}
PLAN = [("water", 20, -1.0, 1.5), ("water", 50, 0.8, 1.0), ("water", 200, -2.6, 0.6),
        ("DMSO", 20, -6.0, 2.5), ("BCA_working_reagent", 200, -0.9, 0.8), ("serum", 50, 0.5, 1.0)]


def exact(rng, n, mean, cv):
    """n values with exactly this mean and sample CV."""
    raw = [rng.gauss(0, 1) for _ in range(n)]
    m = sum(raw) / n
    s = (sum((x - m) ** 2 for x in raw) / (n - 1)) ** 0.5
    return [mean + (x - m) / s * mean * cv / 100 for x in raw]


def main():
    rng = random.Random(8655)
    lines = ["# instrument: Opentrons OT-2, p300_single_gen2 (left)",
             "# balance: Mettler-Toledo XPR206 (0.01 mg)",
             f"# temperature_C: {T}",
             "# operator: fixture",
             "liquid,target_uL,channel,replicate,mass_mg"]
    for liquid, target, dev, cv in PLAN:
        z = z_factor(DENS[liquid])
        for i, v in enumerate(exact(rng, 10, target * (1 + dev / 100), cv), start=1):
            lines.append(f"{liquid},{target},1,{i},{v / z:.4f}")
    (HERE / "gravimetric_template.csv").write_text("\n".join(lines) + "\n")

    # Mettler SICS stream, cumulative on one vessel: water 100 uL x 10, DEV +0.5%, CV 0.4%
    z = z_factor(DENS["water"])
    vols = exact(rng, 10, 100.5, 0.4)
    total, out = 0.0, ["S S      0.00000 g"]
    for v in vols:
        total += v / z / 1000.0
        out.append(f"S S {total:12.5f} g")
    (HERE / "balance_sics_cumulative.txt").write_text("\n".join(out) + "\n")

    # Sartorius stream, tared each dispense: 10 x 50 uL water, DEV -0.4%, CV 0.9%
    out = [f"N     + {v / z:9.2f} mg" for v in exact(rng, 10, 49.8, 0.9)]
    (HERE / "balance_sartorius_individual.txt").write_text("\n".join(out) + "\n")
    print("wrote gravimetric_template.csv, balance_sics_cumulative.txt, balance_sartorius_individual.txt")


if __name__ == "__main__":
    main()
