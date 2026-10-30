"""Plate-level acceptance scoring (M8).

Well-level validation (validation.py) asks "is this one reading plausible?".
The criteria that decide whether a plate is reportable need every well at
once: the standard-curve fit, blank background, replicate CV, QC recovery,
spike recovery. This module scores those.

Configuration lives in criteria.json under an assay's "plate" block:

    "plate": {
      "library_assay_id": "BIO_002",       # robotic-assays entry these criteria restate
      "model": "linear" | "quadratic" | "4pl",
      "blank_subtract": true,              # subtract mean blank before fitting
      "fit_includes_blank": false,         # fit the 0-concentration points too
      "conc_unit": "ug/mL",
      "compare_models": ["quadratic"],     # informational R2 of other models
      "criteria": [ {"key": ..., "type": ..., "min": ..., "max": ...}, ... ],
      "flags": [ {"type": "signal_above", "value": 2.0, "message": ...} ]
    }

Each criterion "key" is the library's own acceptance_criteria key, so a
verdict always names the rule it enforces (tests hold the two in step).

Plate maps: the M7 form {well: role} still works. The M8 form gives each
well a dict -- {"role": "standard", "conc": 500, "group": "STD5"},
{"role": "sample", "group": "S01", "dilution": 3},
{"role": "qc", "group": "QC1", "expected": 250},
{"role": "positive_control", "group": "P01", "base_group": "S01", "spike": 100}.
Keys starting with "_" are metadata ("_meta": {"conc_unit": ...}).

Verdicts per criterion: PASS, FAIL, INVALID (could not be computed from the
data, e.g. the fit failed), NOT_EVALUATED (the plate map does not contain
what the criterion needs). Plate verdict: FAIL > INVALID > INCOMPLETE > PASS
-- a criterion that was not evaluated never reads as a pass.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

import numpy as np

MODELS = ("linear", "quadratic", "4pl")


# ─────────────────────────────── plate map ───────────────────────────────

def normalize_plate_map(plate_map: dict | None) -> tuple[dict, dict]:
    """Return ({well: {"role": ..., ...}}, meta). Accepts M7 and M8 forms."""
    wells, meta = {}, {}
    for key, value in (plate_map or {}).items():
        if key.startswith("_"):
            if key == "_meta" and isinstance(value, dict):
                meta = dict(value)
            continue
        if isinstance(value, str):
            wells[key] = {"role": value}
        elif isinstance(value, dict) and value.get("role"):
            wells[key] = dict(value)
        else:
            raise ValueError(f"plate map well {key!r}: expected a role string or a dict with 'role'")
    return wells, meta


def role_of(plate_map_wells: dict, well: str, default: str = "sample") -> str:
    return plate_map_wells.get(well, {}).get("role", default)


# ──────────────────────────────── models ─────────────────────────────────

def _four_pl(x, a, b, c, d):
    """a = response at zero dose (bottom for a rising curve), d = response at
    infinite dose (top), c = inflection point, b = slope."""
    x = np.asarray(x, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return d + (a - d) / (1.0 + np.power(np.clip(x, 0, None) / c, b))


def fit_model(model: str, x: list, y: list) -> dict:
    """Fit `model`; returns {"model", "params", "r2", "n_points"} or raises ValueError."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}")
    need = {"linear": 2, "quadratic": 3, "4pl": 4}[model]
    if len(set(x.tolist())) < need:
        raise ValueError(f"{model} fit needs >= {need} distinct concentrations, got {len(set(x.tolist()))}")

    if model == "linear":
        coef = np.polyfit(x, y, 1)
        params = {"slope": float(coef[0]), "intercept": float(coef[1])}
        pred = np.polyval(coef, x)
    elif model == "quadratic":
        coef = np.polyfit(x, y, 2)
        params = {"a2": float(coef[0]), "a1": float(coef[1]), "a0": float(coef[2])}
        pred = np.polyval(coef, x)
    else:
        from scipy.optimize import curve_fit
        pos = x[x > 0]
        p0 = [float(y[np.argmin(x)]), 1.0, float(np.median(pos)) if len(pos) else 1.0,
              float(y[np.argmax(x)])]
        lower = [-np.inf, 0.01, float(pos.min()) / 100 if len(pos) else 1e-9, -np.inf]
        upper = [np.inf, 20.0, float(pos.max()) * 100 if len(pos) else np.inf, np.inf]
        try:
            popt, _ = curve_fit(_four_pl, x, y, p0=p0, bounds=(lower, upper), maxfev=20000)
        except Exception as exc:  # scipy raises RuntimeError / ValueError
            raise ValueError(f"4PL fit did not converge: {exc}") from exc
        a, b, c, d = (float(v) for v in popt)
        params = {"a": a, "b": b, "c": c, "d": d,
                  "bottom": min(a, d), "top": max(a, d)}
        pred = _four_pl(x, *popt)

    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {"model": model, "params": params, "r2": r2, "n_points": int(len(x))}


def inverse(fit: dict, y: float, x_range: tuple[float, float]) -> float | None:
    """Concentration for response y, or None if the model cannot invert it."""
    p, model = fit["params"], fit["model"]
    if model == "linear":
        return (y - p["intercept"]) / p["slope"] if p["slope"] else None
    if model == "quadratic":
        roots = np.roots([p["a2"], p["a1"], p["a0"] - y])
        real = [float(r.real) for r in roots if abs(r.imag) < 1e-9]
        lo, hi = x_range
        span = hi - lo if hi > lo else 1.0
        # choose the root on the monotonic branch that covers the standards
        inside = [r for r in real if lo - span <= r <= hi + span]
        if not inside:
            return None
        return min(inside, key=lambda r: abs(r - (lo + hi) / 2))
    a, b, c, d = p["a"], p["b"], p["c"], p["d"]
    if (y - a) * (d - y) <= 0:          # outside the asymptotes
        return None
    return c * ((a - d) / (y - d) - 1.0) ** (1.0 / b)


# ──────────────────────────────── scoring ────────────────────────────────

def _cv(values: list) -> float | None:
    if len(values) < 2:
        return None
    m = statistics.fmean(values)
    return statistics.stdev(values) / m * 100.0 if m else None


def _verdict(value, lo, hi):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "INVALID"
    if lo is not None and value < lo:
        return "FAIL"
    if hi is not None and value > hi:
        return "FAIL"
    return "PASS"


def _limit_text(lo, hi, unit=""):
    u = f" {unit}" if unit else ""
    if lo is not None and hi is not None:
        return f"{lo}-{hi}{u}"
    return f">= {lo}{u}" if lo is not None else f"<= {hi}{u}"


def score_plate(values: dict, plate_map: dict, plate_cfg: dict) -> dict:
    """Score one plate. `values` is {well: signal}; `plate_map` as above."""
    wells, meta = normalize_plate_map(plate_map)
    conc_unit = meta.get("conc_unit") or plate_cfg.get("conc_unit", "")
    model = plate_cfg.get("model", "linear")
    criteria_out, flags = [], []

    def crit_result(c, value, verdict, detail=None, reason=None):
        out = {"key": c["key"], "type": c["type"], "value": value,
               "limit": _limit_text(c.get("min"), c.get("max"), c.get("unit", "")),
               "verdict": verdict}
        if detail is not None:
            out["detail"] = detail
        if reason:
            out["reason"] = reason
        criteria_out.append(out)

    standards = {w: e for w, e in wells.items() if e["role"] == "standard"}
    if not standards:
        return {"verdict": "NOT_EVALUATED",
                "reason": "plate map defines no standard wells; plate-level criteria were not scored",
                "criteria": [], "flags": []}
    for w, e in standards.items():          # a malformed map is an error, not a verdict
        if e.get("conc") is None:
            raise ValueError(f"standard well {w} has no 'conc' in the plate map")

    # ---- blank ----------------------------------------------------------
    blank_wells = [w for w, e in wells.items() if e["role"] == "blank" and values.get(w) is not None]
    blank_mean = statistics.fmean(values[w] for w in blank_wells) if blank_wells else None
    offset = blank_mean if (plate_cfg.get("blank_subtract") and blank_mean is not None) else 0.0
    if plate_cfg.get("blank_subtract") and blank_mean is None:
        return {"verdict": "INVALID", "reason": "blank subtraction configured but no blank wells were read",
                "criteria": [], "flags": []}

    def corrected(w):
        v = values.get(w)
        return None if v is None else v - offset

    # ---- standard curve -------------------------------------------------
    xs, ys, missing_std = [], [], []
    points = [(w, e) for w, e in standards.items()]
    if plate_cfg.get("fit_includes_blank"):
        points += [(w, {**e, "conc": e.get("conc", 0.0)}) for w, e in wells.items() if e["role"] == "blank"]
    for w, e in points:
        if e.get("conc") is None:
            raise ValueError(f"standard well {w} has no 'conc' in the plate map")
        v = corrected(w)
        if v is None:
            missing_std.append(w)
            continue
        if e["conc"] == 0 and not plate_cfg.get("fit_includes_blank"):
            continue
        xs.append(float(e["conc"]))
        ys.append(float(v))

    fit, fit_error = None, None
    try:
        fit = fit_model(model, xs, ys)
    except ValueError as exc:
        fit_error = str(exc)
    std_conc = [x for x in xs if x > 0] or [0.0]
    x_range = (min(std_conc), max(std_conc))
    std_signal = [y for x, y in zip(xs, ys) if x > 0]
    y_range = (min(std_signal), max(std_signal)) if std_signal else (None, None)

    alt = {}
    for m in plate_cfg.get("compare_models", []):
        try:
            alt[m] = round(fit_model(m, xs, ys)["r2"], 5)
        except ValueError as exc:
            alt[m] = f"fit failed: {exc}"

    # ---- groups ---------------------------------------------------------
    groups = defaultdict(list)
    for w, e in wells.items():
        if e["role"] in ("blank",):
            continue
        g = e.get("group") or (f"STD@{e.get('conc')}" if e["role"] == "standard" else w)
        groups[(e["role"], g)].append(w)

    def back_calc(ws, dilution=1.0):
        sig = [corrected(w) for w in ws if corrected(w) is not None]
        if not sig or fit is None:
            return None, None
        mean_sig = statistics.fmean(sig)
        conc = inverse(fit, mean_sig, x_range)
        return mean_sig, (None if conc is None else conc * dilution)

    samples_out = []
    for (role, g), ws in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        if role not in ("sample", "qc", "positive_control"):
            continue
        dil = float(wells[ws[0]].get("dilution", 1.0))
        mean_sig, conc = back_calc(ws, dil)
        raw = [values[w] for w in ws if values.get(w) is not None]
        in_range = (mean_sig is not None and y_range[0] is not None
                    and y_range[0] <= mean_sig <= y_range[1])
        samples_out.append({
            "group": g, "role": role, "wells": sorted(ws), "mean_signal": None if mean_sig is None else round(mean_sig, 4),
            "concentration": None if conc is None else round(conc, 4), "unit": conc_unit,
            "dilution": dil, "cv_pct": None if _cv(raw) is None else round(_cv(raw), 2),
            "within_standard_range": bool(in_range)})
        if not in_range:
            flags.append({"type": "outside_standard_range", "group": g, "wells": sorted(ws),
                          "message": "mean signal outside the standard curve's measured range -- "
                                     "re-run at an adjusted dilution; concentration is extrapolated or undefined"})

    # ---- criteria -------------------------------------------------------
    for c in plate_cfg.get("criteria", []):
        t, lo, hi = c["type"], c.get("min"), c.get("max")
        if t == "curve_r2":
            if fit is None:
                crit_result(c, None, "INVALID", reason=fit_error)
            else:
                v = round(fit["r2"], 5)
                crit_result(c, v, _verdict(v, lo, hi), detail={"model": model, "n_points": fit["n_points"]})
        elif t == "blank_mean_max" or t == "blank_mean":
            if blank_mean is None:
                crit_result(c, None, "NOT_EVALUATED", reason="no blank wells in the plate map")
            else:
                v = round(blank_mean, 4)
                crit_result(c, v, _verdict(v, lo, hi), detail={"wells": sorted(blank_wells)})
        elif t == "fit_param":
            if fit is None:
                crit_result(c, None, "INVALID", reason=fit_error)
            elif c["param"] not in fit["params"]:
                crit_result(c, None, "INVALID", reason=f"model {model!r} has no parameter {c['param']!r}")
            else:
                v = round(fit["params"][c["param"]], 4)
                # asymptotes are in corrected units; report raw if a blank was subtracted
                if offset and c["param"] in ("bottom", "top", "a", "d"):
                    v = round(v + offset, 4)
                crit_result(c, v, _verdict(v, lo, hi), detail={"param": c["param"]})
        elif t == "replicate_cv_max":
            roles = set(c.get("applies_to", ["sample", "standard"]))
            cvs = {}
            for (role, g), ws in groups.items():
                if role not in roles or len(ws) < 2:
                    continue
                if role == "standard" and float(wells[ws[0]].get("conc") or 0) == 0:
                    continue
                raw = [values[w] for w in ws if values.get(w) is not None]
                cv = _cv(raw)
                if cv is not None:
                    cvs[str(g)] = round(cv, 2)
            if not cvs:
                crit_result(c, None, "NOT_EVALUATED", reason="no replicate groups for roles " + ", ".join(sorted(roles)))
            else:
                worst = max(cvs.values())
                failing = {g: v for g, v in cvs.items() if hi is not None and v > hi}
                crit_result(c, worst, _verdict(worst, lo, hi),
                            detail={"groups_scored": len(cvs), "failing_groups": failing})
        elif t == "qc_recovery":
            recs = {}
            for s in samples_out:
                if s["role"] != "qc":
                    continue
                exp = wells[s["wells"][0]].get("expected")
                if exp and s["concentration"] is not None:
                    recs[s["group"]] = round(s["concentration"] / float(exp) * 100.0, 2)
                elif exp:
                    recs[s["group"]] = None
            if not recs:
                crit_result(c, None, "NOT_EVALUATED",
                            reason="plate map has no qc wells with an 'expected' concentration")
            elif any(v is None for v in recs.values()):
                crit_result(c, None, "INVALID", detail={"recovery_pct": recs},
                            reason="a QC signal could not be back-calculated")
            else:
                verdicts = [_verdict(v, lo, hi) for v in recs.values()]
                worst = max(recs.values(), key=lambda v: abs(v - 100.0))
                crit_result(c, worst, "FAIL" if "FAIL" in verdicts else "PASS",
                            detail={"recovery_pct": recs})
        elif t == "spike_recovery":
            by_group = {s["group"]: s for s in samples_out}
            recs = {}
            for s in samples_out:
                if s["role"] != "positive_control":
                    continue
                e = wells[s["wells"][0]]
                base = by_group.get(e.get("base_group"))
                spike = e.get("spike")
                if base is None or not spike:
                    continue
                if s["concentration"] is None or base["concentration"] is None:
                    recs[s["group"]] = None
                else:
                    recs[s["group"]] = round((s["concentration"] - base["concentration"]) / float(spike) * 100.0, 2)
            if not recs:
                crit_result(c, None, "NOT_EVALUATED",
                            reason="plate map has no positive_control wells with 'base_group' and 'spike'")
            elif any(v is None for v in recs.values()):
                crit_result(c, None, "INVALID", detail={"recovery_pct": recs},
                            reason="a spiked or base sample could not be back-calculated")
            else:
                verdicts = [_verdict(v, lo, hi) for v in recs.values()]
                worst = max(recs.values(), key=lambda v: abs(v - 100.0))
                crit_result(c, worst, "FAIL" if "FAIL" in verdicts else "PASS",
                            detail={"recovery_pct": recs})
        else:
            crit_result(c, None, "INVALID", reason=f"unknown criterion type {t!r}")

    for f in plate_cfg.get("flags", []):
        if f["type"] == "signal_above":
            hits = sorted(w for w, v in values.items()
                          if v is not None and v > f["value"] and w in wells)
            if hits:
                flags.append({"type": "signal_above", "threshold": f["value"], "wells": hits,
                              "message": f.get("message", "")})

    if missing_std:
        flags.append({"type": "missing_standard_reads", "wells": sorted(missing_std),
                      "message": "standard wells with no reading were excluded from the fit"})

    verdicts = [c["verdict"] for c in criteria_out]
    if "FAIL" in verdicts:
        plate_verdict = "FAIL"
    elif "INVALID" in verdicts:
        plate_verdict = "INVALID"
    elif "NOT_EVALUATED" in verdicts or not verdicts:
        plate_verdict = "INCOMPLETE"
    else:
        plate_verdict = "PASS"

    return {
        "verdict": plate_verdict,
        "library_assay_id": plate_cfg.get("library_assay_id"),
        "fit": None if fit is None else {
            "model": fit["model"], "r2": round(fit["r2"], 5), "n_points": fit["n_points"],
            "params": {k: round(v, 6) for k, v in fit["params"].items()}},
        "fit_error": fit_error,
        "alternative_fits_r2": alt,
        "blank_mean": None if blank_mean is None else round(blank_mean, 4),
        "blank_subtracted": bool(offset),
        "criteria": criteria_out,
        "samples": samples_out,
        "flags": flags,
    }
