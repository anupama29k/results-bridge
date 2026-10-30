"""Gravimetric liquid-class verification (M9).

A liquid handler is verified by dispensing a target volume repeatedly onto
an analytical balance and converting each mass to a volume. Per liquid x
target volume x channel this module reports:

    mean volume, systematic error %DEV = (mean - V0) / V0 * 100,
    random error %CV = s / mean * 100, n, and PASS / FAIL / INVALID

against the limits in gravimetric.json. This is the liquid-class table of a
validation pack.

MASS -> VOLUME (ISO 8655-6 approach)
    V [uL] = m [mg] / (rho_L - rho_A) * (1 - rho_A / rho_B)
    rho_L  liquid density [g/mL]: water from temperature (Tanaka et al. 2001,
           ITS-90); any other liquid from gravimetric.json or the file header.
           A liquid with no known density is INVALID -- a serum run is never
           silently converted as if it were water.
    rho_A  air density, default 0.0012 g/mL; rho_B balance weight density,
           8.0 g/mL. At 20 C this gives Z = 1.0029 uL/mg (ISO 8655-6 table).

INPUT FORMATS
    1. Template CSV (recommended; also maps LabX-style exports). '#' lines are
       metadata ("# temperature_C: 21.4"); then a header with columns
       (case-insensitive aliases):
         liquid | target_uL | channel (opt.) | replicate (opt.) |
         mass_mg  or  mass_g  or  weight + unit | temperature_C (opt.)
    2. Balance print stream: Mettler-Toledo SICS lines ("S S  0.02345 g") or
       Sartorius lines ("+ 0.02345 g" / "N + 0.02345 g"). No liquid or target
       in the stream, so the caller passes them. Readings are either one per
       tared dispense ("individual") or a running total on one vessel
       ("cumulative"); mode "auto" decides and reports which it used.

Limits are project defaults, not a standard: vendor accuracy specs are a
best case, and ISO 8655-2 limits are written for hand pipettes. Set them per
method in gravimetric.json.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import statistics
from collections import defaultdict

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "gravimetric.json")

WATER_ALIASES = {"water", "h2o", "di water", "milliq", "milli-q", "dh2o", "ddh2o"}

_SICS = re.compile(r"^\s*S\s+S\s+([-+]?\s*\d+(?:\.\d+)?)\s*(mg|g)\b", re.I)
_SARTORIUS = re.compile(r"^\s*(?:N\s+)?([-+])\s*(\d+(?:\.\d+)?)\s*(mg|g)\b", re.I)

COLUMN_ALIASES = {
    "liquid": ("liquid", "liquid_class", "reagent", "liquid class"),
    "target_uL": ("target_ul", "target_volume_ul", "target volume (ul)", "volume_ul", "target"),
    "channel": ("channel", "tip", "well", "pipette_channel"),
    "replicate": ("replicate", "rep", "n", "dispense"),
    "mass_mg": ("mass_mg", "mass (mg)", "weight_mg", "weight (mg)"),
    "mass_g": ("mass_g", "mass (g)", "weight_g", "weight (g)"),
    "weight": ("weight", "mass", "net", "net weight"),
    "unit": ("unit", "units"),
    "temperature_C": ("temperature_c", "temp_c", "temperature (c)", "temperature"),
}


def load_config() -> dict:
    with open(_CONFIG_PATH) as fh:
        return json.load(fh)


# ───────────────────────────── physics ─────────────────────────────

def water_density(t_c: float) -> float:
    """Air-free water density in g/mL (Tanaka et al., Metrologia 38, 2001)."""
    a1, a2, a3, a4, a5 = -3.983035, 301.797, 522528.9, 69.34881, 999.974950
    return a5 * (1 - (t_c + a1) ** 2 * (t_c + a2) / (a3 * (t_c + a4))) / 1000.0


def z_factor(liquid_density: float, air_density: float = 0.0012, weight_density: float = 8.0) -> float:
    """uL per mg, including air buoyancy."""
    return (1.0 / (liquid_density - air_density)) * (1.0 - air_density / weight_density)


# ───────────────────────────── parsing ─────────────────────────────

def _to_mg(value: float, unit: str) -> float:
    u = unit.strip().lower()
    if u == "mg":
        return value
    if u == "g":
        return value * 1000.0
    if u in ("ug", "µg"):
        return value / 1000.0
    raise ValueError(f"unsupported mass unit {unit!r}")


def _find(header_lower, key):
    for alias in COLUMN_ALIASES[key]:
        if alias in header_lower:
            return header_lower.index(alias)
    return None


def parse_template_csv(text: str) -> tuple[dict, list]:
    """Returns (metadata, readings) where each reading is
    {liquid, target_uL, channel, replicate, mass_mg, temperature_C}."""
    meta, body = {}, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            if ":" in s:
                k, v = s[1:].split(":", 1)
                meta[k.strip()] = v.strip()
        elif s:
            body.append(line)
    rows = list(csv.reader(io.StringIO("\n".join(body))))
    if not rows:
        raise ValueError("no data rows")
    header = [h.strip().lower() for h in rows[0]]
    idx = {k: _find(header, k) for k in COLUMN_ALIASES}
    if idx["liquid"] is None or idx["target_uL"] is None:
        raise ValueError("template needs 'liquid' and 'target_uL' columns")
    if idx["mass_mg"] is None and idx["mass_g"] is None and idx["weight"] is None:
        raise ValueError("template needs a mass column (mass_mg, mass_g, or weight + unit)")
    readings = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        cell = lambda k: row[idx[k]].strip() if idx[k] is not None and idx[k] < len(row) else ""
        try:
            if idx["mass_mg"] is not None and cell("mass_mg"):
                mass = float(cell("mass_mg"))
            elif idx["mass_g"] is not None and cell("mass_g"):
                mass = float(cell("mass_g")) * 1000.0
            else:
                unit = cell("unit") or meta.get("unit", "")
                if not unit:
                    raise ValueError("weight column without a unit")
                mass = _to_mg(float(cell("weight")), unit)
            target = float(cell("target_uL"))
        except ValueError as exc:
            raise ValueError(f"row {n}: {exc}") from exc
        temp = cell("temperature_C")
        readings.append({
            "liquid": cell("liquid"), "target_uL": target,
            "channel": cell("channel") or "1", "replicate": cell("replicate") or str(len(readings) + 1),
            "mass_mg": mass, "temperature_C": float(temp) if temp else None})
    return meta, readings


def parse_balance_stream(text: str) -> list[float]:
    """Stable readings in mg from a Mettler SICS or Sartorius print stream."""
    out = []
    for line in text.splitlines():
        m = _SICS.match(line)
        if m:
            out.append(_to_mg(float(m.group(1).replace(" ", "")), m.group(2)))
            continue
        m = _SARTORIUS.match(line)
        if m:
            sign = -1.0 if m.group(1) == "-" else 1.0
            out.append(sign * _to_mg(float(m.group(2)), m.group(3)))
    if not out:
        raise ValueError("no stable balance readings recognised (Mettler SICS or Sartorius format)")
    return out


def stream_to_readings(values_mg: list, liquid: str, target_uL: float, mode: str = "auto",
                       channel: str = "1") -> tuple[str, list]:
    """Per-dispense masses from a stream. Returns (mode_used, readings)."""
    if mode == "auto":
        increasing = all(b > a for a, b in zip(values_mg, values_mg[1:]))
        first_small = abs(values_mg[0]) < 0.5 * max(abs(v) for v in values_mg[1:]) if len(values_mg) > 2 else False
        mode = "cumulative" if increasing and len(values_mg) > 2 and (first_small or values_mg[-1] > 3 * values_mg[1]) else "individual"
    if mode == "cumulative":
        masses = [b - a for a, b in zip(values_mg, values_mg[1:])]
    elif mode == "individual":
        masses = list(values_mg)
    else:
        raise ValueError(f"mode must be auto, individual or cumulative, not {mode!r}")
    return mode, [{"liquid": liquid, "target_uL": float(target_uL), "channel": channel,
                   "replicate": str(i + 1), "mass_mg": m, "temperature_C": None}
                  for i, m in enumerate(masses)]


# ───────────────────────────── scoring ─────────────────────────────

def limits_for(volume_uL: float, cfg: dict, liquid: str) -> dict:
    override = cfg.get("liquid_limits", {}).get(liquid.lower())
    bands = override or cfg["limits"]
    for band in sorted(bands, key=lambda b: b["max_volume_uL"]):
        if volume_uL <= band["max_volume_uL"]:
            return band
    raise ValueError(f"no limit band covers {volume_uL} uL")


def density_for(liquid: str, temperature_C: float, cfg: dict, meta: dict) -> float | None:
    name = liquid.strip().lower()
    if name in WATER_ALIASES:
        return water_density(temperature_C)
    hdr = meta.get(f"density_{name}")      # per-file override, e.g. "# density_serum: 1.024"
    if hdr:
        return float(hdr)
    table = {k.lower(): v for k, v in cfg.get("densities_g_per_mL", {}).items()}
    return table.get(name)


def score_gravimetric(readings: list, cfg: dict, meta: dict | None = None,
                      temperature_C: float | None = None) -> dict:
    meta = meta or {}
    default_t = temperature_C if temperature_C is not None else (
        float(meta["temperature_C"]) if meta.get("temperature_C") else None)
    air = float(meta.get("air_density_g_per_mL", cfg.get("air_density_g_per_mL", 0.0012)))
    groups = defaultdict(list)
    for r in readings:
        groups[(r["liquid"], r["target_uL"], r["channel"])].append(r)

    out, flags = [], []
    for (liquid, target, channel), rs in sorted(groups.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1], kv[0][2])):
        temps = [r["temperature_C"] if r["temperature_C"] is not None else default_t for r in rs]
        row = {"liquid": liquid, "target_uL": target, "channel": channel, "n": len(rs)}
        try:
            band = limits_for(target, cfg, liquid)
        except ValueError as exc:
            out.append({**row, "verdict": "INVALID", "reason": str(exc)})
            continue
        row["limit_dev_pct"], row["limit_cv_pct"] = band["dev_pct"], band["cv_pct"]
        if any(t is None for t in temps):
            out.append({**row, "verdict": "INVALID",
                        "reason": "no temperature: give a temperature_C column, a '# temperature_C:' header, or the temperature_C argument"})
            continue
        dens = [density_for(liquid, t, cfg, meta) for t in temps]
        if any(d is None for d in dens):
            out.append({**row, "verdict": "INVALID",
                        "reason": f"no density for liquid {liquid!r}: add it to gravimetric.json "
                                  f"densities_g_per_mL or a '# density_{liquid.lower()}:' header"})
            continue
        vols = [r["mass_mg"] * z_factor(d, air) for r, d in zip(rs, dens)]
        mean = statistics.fmean(vols)
        sd = statistics.stdev(vols) if len(vols) > 1 else None
        dev = (mean - target) / target * 100.0
        cv = sd / mean * 100.0 if sd is not None and mean else None
        row.update({"mean_volume_uL": round(mean, 4), "sd_uL": None if sd is None else round(sd, 4),
                    "dev_pct": round(dev, 3), "cv_pct": None if cv is None else round(cv, 3),
                    "z_factor_uL_per_mg": round(z_factor(statistics.fmean(dens), air), 5),
                    "density_g_per_mL": round(statistics.fmean(dens), 5),
                    "temperature_C": round(statistics.fmean(temps), 2)})
        if len(rs) < cfg.get("min_replicates", 10):
            row.update({"verdict": "INVALID",
                        "reason": f"{len(rs)} dispenses < {cfg.get('min_replicates', 10)} required"})
        elif abs(dev) > band["dev_pct"] or cv > band["cv_pct"]:
            why = []
            if abs(dev) > band["dev_pct"]:
                why.append(f"|DEV| {abs(dev):.2f}% > {band['dev_pct']}%")
            if cv > band["cv_pct"]:
                why.append(f"CV {cv:.2f}% > {band['cv_pct']}%")
            row.update({"verdict": "FAIL", "reason": "; ".join(why)})
        else:
            row.update({"verdict": "PASS", "reason": None})
        if sd:
            out_reps = [rs[i]["replicate"] for i, v in enumerate(vols) if abs(v - mean) > 3 * sd]
            if out_reps:
                flags.append({"type": "possible_outlier", "liquid": liquid, "target_uL": target,
                              "channel": channel, "replicates": out_reps,
                              "message": "dispense > 3 SD from the mean -- investigate (bubble, "
                                         "droplet, evaporation); not removed automatically"})
        out.append(row)

    counts = {"PASS": 0, "FAIL": 0, "INVALID": 0}
    for r in out:
        counts[r["verdict"]] += 1
    overall = "ALL_PASS" if out and counts["FAIL"] == counts["INVALID"] == 0 else "ATTENTION_REQUIRED"
    return {"summary": counts, "overall": overall, "liquid_classes": out, "flags": flags,
            "method": "ISO 8655-6 style: V = m / (rho_L - rho_A) * (1 - rho_A / rho_B)",
            "limits_source": cfg.get("limits_source")}
