"""Validation-pack assembler (M10).

One manifest describes one engagement: the generated protocol and its
simulator log, the gravimetric files, the performance-qualification (PQ)
plate runs and who did what. build_pack() re-scores every input through
the same code the service uses (plate_scoring, gravimetric), hashes every
file, and renders a self-contained HTML pack (and PDF, if weasyprint is
installed):

     0  Cover and status              7  Performance qualification runs
     1  Scope and intended use        8  Deviations and observations
     2  System record                 9  Known limitations
     3  Deck layout and fill sheet   10  Change control
     4  Protocol verification        11  Traceability
     5  Acceptance criteria          12  Training record and sign-off
     6  Liquid-class verification

The acceptance status is COMPUTED, never typed: ACCEPTED only when the
protocol simulated cleanly, every required liquid class passed on every
channel, and the required number of consecutive PQ runs passed every
plate-level criterion. A pack whose manifest says "demonstration": true is
watermarked on every page and its status reads DEMONSTRATION.

    python -m results_bridge.pack demo/elisa/manifest.json --out pack.html [--pdf pack.pdf]

Research use: a pack is fit-for-purpose evidence for a research method. It
is not GMP/GLP validation and not 21 CFR Part 11 computer-system validation.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import html
import json
import math
import os
import subprocess
import sys

from . import tools
from .gravimetric import load_config as load_grav_config
from .validation import load_criteria

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECTION_TITLES = [
    "Scope and intended use", "System record", "Deck layout and fill sheet",
    "Protocol verification", "Acceptance criteria", "Liquid-class verification (OQ)",
    "Performance qualification runs (PQ)", "Deviations and observations",
    "Known limitations", "Change control", "Traceability", "Training record and sign-off",
]


# ─────────────────────────────── inputs ────────────────────────────────

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(path: str) -> str:
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def parse_protocol(path: str) -> dict:
    """Header sections, metadata and requirements of a generated Opentrons protocol."""
    src = open(path).read()
    tree = ast.parse(src)
    doc = ast.get_docstring(tree) or ""
    out = {"metadata": {}, "requirements": {}, "sections": {}, "title": doc.splitlines()[0] if doc else ""}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("metadata", "requirements"):
                out[node.targets[0].id] = ast.literal_eval(node.value)
    current = None
    heads = {"DECK": "deck", "FILL BEFORE RUN": "fill", "OPERATOR NOTES": "notes",
             "LIBRARY RECONCILIATION": "reconciliation", "ACCEPTANCE CRITERIA": "criteria"}
    for line in doc.splitlines():
        key = next((v for k, v in heads.items() if line.startswith(k)), None)
        if key:
            current = key
            out["sections"][current] = []
            continue
        if current and line.startswith("  ") and line.strip():
            text = line.strip()
            if text.startswith("- "):
                out["sections"][current].append(text[2:])
            elif current in ("notes", "reconciliation") and out["sections"][current]:
                out["sections"][current][-1] += " " + text
            else:
                out["sections"][current].append(text)
        elif current and not line.strip():
            current = None
    return out


def parse_sim_log(path: str) -> dict:
    lines = open(path).read().splitlines()
    return {
        "lines": len(lines),
        "tip_pickups": sum("Picking up tip" in l for l in lines),
        "aspirations": sum(l.lstrip().startswith("Aspirating") for l in lines),
        "dispenses": sum(l.lstrip().startswith("Dispensing") for l in lines),
        "pauses": [l.split("Pausing robot operation:", 1)[1].strip() for l in lines if "Pausing robot operation:" in l],
        "delays": [l.strip() for l in lines if l.strip().startswith("Delaying for")],
    }


# ─────────────────────────────── scoring ───────────────────────────────

def _match_liquid(row, req):
    return (row["liquid"].lower() == req["liquid"].lower()
            and float(row["target_uL"]) == float(req["target_uL"])
            and (not req.get("pipette") or row.get("pipette", "") == req["pipette"]))


def evaluate(manifest: dict, base: str) -> dict:
    p = lambda rel: os.path.normpath(os.path.join(base, rel))
    crit_all = load_criteria()
    rb_id = manifest["assay"]["results_bridge_assay_id"]
    crit = crit_all[rb_id]

    proto_path = p(manifest["protocol"]["path"])
    protocol = parse_protocol(proto_path)
    sim = parse_sim_log(p(manifest["protocol"]["simulator_log"]))
    sim_ok = manifest["protocol"].get("simulator_exit_code") == 0 and sim["tip_pickups"] > 0

    grav_results = []
    for g in manifest.get("gravimetric", []):
        r = tools.check_gravimetric(p(g["path"]), g.get("temperature_C"), g.get("liquid"),
                                    g.get("target_uL"), g.get("mode", "auto"))
        grav_results.append({**g, "result": r, "sha256": sha256(p(g["path"]))})
    grav_rows = [row for g in grav_results for row in g["result"]["liquid_classes"]]

    liquid_status = []
    for req in manifest.get("liquid_classes_required", []):
        rows = [r for r in grav_rows if _match_liquid(r, req)]
        if not rows:
            verdict, why = "NOT VERIFIED", "no gravimetric data for this liquid class"
        elif all(r["verdict"] == "PASS" for r in rows):
            verdict, why = "PASS", f"{len(rows)} channel(s) passed"
        else:
            bad = [f"ch {r['channel']}: {r['verdict']}" for r in rows if r["verdict"] != "PASS"]
            verdict, why = "FAIL", "; ".join(bad)
        liquid_status.append({**req, "verdict": verdict, "detail": why, "channels": len(rows)})

    runs = []
    for run in manifest.get("runs", []):
        res = tools.check_file(p(run["file"]), rb_id, p(run["plate_map"]))
        plate = res.get("plate") or {"verdict": "NOT_EVALUATED", "criteria": []}
        passed = plate["verdict"] == "PASS" and res["overall"] == "ALL_PASS"
        runs.append({**run, "result": res, "plate": plate, "passed": passed,
                     "sha256": sha256(p(run["file"])), "plate_map_sha256": sha256(p(run["plate_map"]))})

    need = int(manifest.get("required_consecutive_passes", 3))
    best = streak = 0
    for r in runs:
        streak = streak + 1 if r["passed"] else 0
        best = max(best, streak)

    reasons = []
    if not sim_ok:
        reasons.append("protocol did not complete simulation")
    if not liquid_status:
        reasons.append("no liquid classes listed as required")
    reasons += [f"liquid class {l['liquid']} {l['target_uL']:g} uL ({l.get('pipette') or 'any pipette'}): {l['verdict']}"
                for l in liquid_status if l["verdict"] != "PASS"]
    if best < need:
        reasons.append(f"{best} consecutive PQ run(s) passed; {need} required")
    accepted = not reasons
    demo = bool(manifest.get("demonstration"))
    status = ("DEMONSTRATION: criteria met on simulated data" if demo and accepted else
              "DEMONSTRATION: criteria not met on simulated data" if demo else
              "ACCEPTED" if accepted else "NOT ACCEPTED")

    deviations = []
    for r in runs:
        for c in r["plate"].get("criteria", []):
            if c["verdict"] != "PASS":
                deviations.append({"kind": "Acceptance failure" if c["verdict"] == "FAIL" else "Not evaluated",
                                   "where": r["run_id"], "text": f"{c['key']}: {c['verdict']} "
                                   f"(value {c['value']}, limit {c['limit']}){' -- ' + c['reason'] if c.get('reason') else ''}"})
        for f in r["plate"].get("flags", []):
            what = f.get("group") or ", ".join(f.get("wells", [])[:6])
            deviations.append({"kind": "Observation", "where": r["run_id"],
                               "text": f"{f['type'].replace('_', ' ')} -- {what}: {f.get('message', '')}"})
    for g in grav_results:
        for row in g["result"]["liquid_classes"]:
            if row["verdict"] != "PASS":
                deviations.append({"kind": "Liquid class", "where": os.path.basename(g["path"]),
                                   "text": f"{row['liquid']} {row['target_uL']:g} uL ch {row['channel']}: "
                                           f"{row['verdict']} -- {row.get('reason')}"})
        for f in g["result"]["flags"]:
            deviations.append({"kind": "Observation", "where": os.path.basename(g["path"]),
                               "text": f"possible outlier: {f['liquid']} {f['target_uL']:g} uL ch {f['channel']} "
                                       f"replicate(s) {', '.join(f['replicates'])}"})
    for note in protocol["sections"].get("reconciliation", []):
        deviations.append({"kind": "Planned deviation from library method", "where": "protocol", "text": note})

    inputs = [("Protocol", proto_path), ("Simulator log", p(manifest["protocol"]["simulator_log"]))]
    inputs += [("Gravimetric data", p(g["path"])) for g in manifest.get("gravimetric", [])]
    for r in manifest.get("runs", []):
        inputs += [(f"{r['run_id']} reader export", p(r["file"])), (f"{r['run_id']} plate map", p(r["plate_map"]))]
    inputs += [("criteria.json", os.path.join(_REPO, "criteria.json")),
               ("gravimetric.json", os.path.join(_REPO, "gravimetric.json"))]
    trace = [{"what": w, "file": os.path.basename(path), "sha256": sha256(path)} for w, path in inputs]

    from .parsers import _LIB
    library_entry = None
    try:
        from .library import load_all_assays
        hits = [e for e in load_all_assays() if e.get("assay_id") == manifest["assay"].get("library_assay_id")]
        library_entry = hits[-1] if hits else None
    except Exception:
        library_entry = None

    return {
        "manifest": manifest, "status": status, "accepted": accepted, "reasons": reasons,
        "demonstration": demo, "protocol": protocol, "protocol_sha256": sha256(proto_path),
        "sim": sim, "sim_ok": sim_ok, "grav": grav_results, "liquid_status": liquid_status,
        "runs": runs, "consecutive_passes": best, "required_passes": need,
        "deviations": deviations, "trace": trace, "criteria": crit, "grav_config": load_grav_config(),
        "library_entry": library_entry,
        "versions": {"results-bridge": git_commit(_REPO), "robotic-assays": git_commit(_LIB),
                     "simulator": manifest["protocol"].get("simulator", "unknown")},
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ─────────────────────────────── charts ────────────────────────────────

def curve_svg(run: dict, plate_map: dict, model: str) -> str:
    """Standard points and fitted curve as inline SVG (log-x for 4PL)."""
    from .plate_scoring import _four_pl, normalize_plate_map
    plate, values = run["plate"], {r["sample_id"]: (r["value"]) for r in run["result"]["results"]}
    fit = plate.get("fit")
    wells, _ = normalize_plate_map(plate_map)
    offset = plate.get("blank_mean") if plate.get("blank_subtracted") else 0.0
    pts = [(float(e["conc"]), values.get(w)) for w, e in wells.items()
           if e["role"] in ("standard", "blank") and values.get(w) is not None]
    if not pts or not fit:
        return ""
    W, H, L, R, T, B = 320, 190, 46, 12, 10, 34
    logx = model == "4pl"
    pos = [x for x, _ in pts if x > 0]
    xmin, xmax = (min(pos) / 2, max(pos) * 1.3) if logx else (0, max(x for x, _ in pts) * 1.05)
    ys = [y for _, y in pts]
    ymin, ymax = 0, max(ys) * 1.1
    def sx(x):
        if logx:
            x = max(x, xmin)
            return L + (math.log10(x) - math.log10(xmin)) / (math.log10(xmax) - math.log10(xmin)) * (W - L - R)
        return L + (x - xmin) / (xmax - xmin) * (W - L - R)
    sy = lambda y: T + (1 - (y - ymin) / (ymax - ymin)) * (H - T - B)
    prm = fit["params"]
    def f(x):
        if model == "linear":
            return prm["slope"] * x + prm["intercept"] + offset
        if model == "quadratic":
            return prm["a2"] * x * x + prm["a1"] * x + prm["a0"] + offset
        return float(_four_pl([x], prm["a"], prm["b"], prm["c"], prm["d"])[0]) + offset
    n = 60
    xs = ([10 ** (math.log10(xmin) + i * (math.log10(xmax) - math.log10(xmin)) / n) for i in range(n + 1)]
          if logx else [xmin + i * (xmax - xmin) / n for i in range(n + 1)])
    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(f(x)):.1f}" for i, x in enumerate(xs)
                    if ymin <= f(x) <= ymax * 1.05)
    dots = "".join(f'<circle cx="{sx(x if x > 0 else xmin):.1f}" cy="{sy(y):.1f}" r="2.6" class="pt"/>' for x, y in pts)
    unit = plate.get("samples", [{}])[0].get("unit", "") if plate.get("samples") else ""
    font = 'font-family="Helvetica, Arial, sans-serif" font-size="8" fill="#555"'
    dots = "".join(f'<circle cx="{sx(x if x > 0 else xmin):.1f}" cy="{sy(y):.1f}" r="2.4" fill="#d85a30"/>'
                   for x, y in pts)
    xlabel = f"concentration ({unit}{', log' if logx else ''})"
    return (f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="Standard curve">'
            f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" stroke="#999" stroke-width="0.6"/>'
            f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="#999" stroke-width="0.6"/>'
            f'<path d="{path}" fill="none" stroke="#185fa5" stroke-width="1.4"/>{dots}'
            f'<text x="{(W + L) / 2:.0f}" y="{H - 10}" text-anchor="middle" {font}>{html.escape(xlabel)}</text>'
            f'<text x="{L - 6}" y="{T + 6}" text-anchor="end" {font}>{ymax:.1f}</text>'
            f'<text x="{L - 6}" y="{H - B}" text-anchor="end" {font}>0</text>'
            f'<text x="{L + 6}" y="{T + 10}" {font}>signal</text>'
            f'<text x="{W - R}" y="{H - B - 8}" text-anchor="end" {font}>{model} fit, R² {fit["r2"]:.4f}</text></svg>')


# ─────────────────────────────── render ────────────────────────────────

def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _badge(v: str) -> str:
    cls = {"PASS": "ok", "ACCEPTED": "ok", "FAIL": "bad", "NOT ACCEPTED": "bad",
           "INVALID": "bad", "NOT VERIFIED": "warn", "NOT_EVALUATED": "warn",
           "INCOMPLETE": "warn"}.get(v, "warn")
    return f'<span class="badge {cls}">{_e(v)}</span>'


def _table(headers, rows, cls=""):
    th = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


CSS = """
@page { size: Letter; margin: 18mm 16mm 18mm 16mm;
  @top-center { content: "__BANNER__"; font: 8pt sans-serif; color: __BANNER_COLOR__; }
  @bottom-left { content: "__PACKID__"; font: 8pt sans-serif; color: #666; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 8pt sans-serif; color: #666; } }
body { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 9.5pt; color: #222; line-height: 1.45; margin: 0 auto; max-width: 820px; padding: 0 16px; }
h1 { font-size: 20pt; font-weight: 500; margin: 0 0 4px; }
h2 { font-size: 13pt; font-weight: 500; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #ccc; page-break-after: avoid; }
h3 { font-size: 10.5pt; font-weight: 500; margin: 14px 0 6px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; page-break-inside: auto; }
th, td { border: 0.5px solid #bbb; padding: 3px 6px; text-align: left; vertical-align: top; font-size: 8.5pt; }
th { background: #f1efe8; font-weight: 500; }
tr { page-break-inside: avoid; }
.badge { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 8pt; font-weight: 500; }
.ok { background: #e1f5ee; color: #085041; } .bad { background: #fcebeb; color: #791f1f; } .warn { background: #faeeda; color: #633806; }
.status { border: 1px solid #ccc; border-radius: 8px; padding: 10px 14px; margin: 14px 0; }
.status.ok { border-color: #5dcaa5; } .status.bad { border-color: #f09595; } .status.warn { border-color: #ef9f27; }
.status .big { font-size: 14pt; font-weight: 500; }
.muted { color: #666; } .mono { font-family: Menlo, Consolas, monospace; font-size: 7.5pt; word-break: break-all; }
.watermark { position: fixed; top: 38%; left: 0; right: 0; text-align: center; transform: rotate(-30deg);
  font-size: 54pt; font-weight: 700; color: rgba(163, 45, 45, 0.08); z-index: -1; }
.demo-banner { background: #fcebeb; color: #791f1f; border-radius: 6px; padding: 6px 10px; margin: 8px 0; font-weight: 500; }
.two { display: flex; gap: 16px; align-items: flex-start; } .two > * { flex: 1; }
.sig td { height: 26px; }
.pb { page-break-before: always; }
"""


def render(ev: dict) -> str:
    m = ev["manifest"]
    a = ev["library_entry"] or {}
    proto = ev["protocol"]
    demo = ev["demonstration"]
    banner = "SIMULATED DATA - DEMONSTRATION ONLY - NOT A VALIDATION" if demo else "Research-use validation pack"
    status_cls = "ok" if ev["accepted"] else "bad"
    if demo:
        status_cls = "warn"
    out = []
    w = out.append
    css_q = lambda t: t.replace("\\", "").replace('"', "'")
    css = (CSS.replace("__BANNER__", css_q(banner))
              .replace("__BANNER_COLOR__", "#a32d2d" if demo else "#666")
              .replace("__PACKID__", css_q(f"{m.get('pack_id')} - {ev['status']}")))
    w(f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(m.get('pack_id'))} validation pack</title>"
      f"<meta name='viewport' content='width=device-width, initial-scale=1'><style>{css}</style></head><body>")
    if demo:
        w("<div class='watermark'>SIMULATED DATA</div>")

    # ---- cover ----------------------------------------------------------
    w(f"<p class='muted'>Validation pack {_e(m.get('pack_id'))}</p><h1>{_e(m.get('title'))}</h1>")
    if demo:
        w("<div class='demo-banner'>Demonstration pack. Every result below is generated from simulated "
          "instrument data to show the structure and scoring of a real pack. It is not evidence about any robot.</div>")
    client = m.get("client", {})
    w(_table(["Item", "Value"], [
        ["Client / site", f"{_e(client.get('name'))} -- {_e(client.get('site'))}"],
        ["Assay", f"{_e(a.get('name') or m['assay'].get('library_assay_id'))} "
                  f"(library {_e(m['assay'].get('library_assay_id'))}, scoring {_e(m['assay']['results_bridge_assay_id'])})"],
        ["Robot", _e(m.get("robot", proto["requirements"].get("robotType")))],
        ["Protocol", f"{_e(proto['metadata'].get('protocolName'))} <span class='mono'>sha256 {ev['protocol_sha256'][:16]}…</span>"],
        ["Prepared by", _e(m.get("prepared_by"))],
        ["Generated", _e(ev["generated"])],
    ]))
    reasons = "".join(f"<li>{_e(r)}</li>" for r in ev["reasons"]) or "<li>All acceptance conditions met.</li>"
    w(f"<div class='status {status_cls}'><div class='big'>Status: {_e(ev['status'])}</div>"
      f"<ul>{reasons}</ul>"
      f"<div class='muted'>Computed from the evidence in this pack: protocol simulation "
      f"{'passed' if ev['sim_ok'] else 'FAILED'}; "
      f"{sum(l['verdict'] == 'PASS' for l in ev['liquid_status'])}/{len(ev['liquid_status'])} required liquid classes passed; "
      f"{ev['consecutive_passes']}/{ev['required_passes']} consecutive PQ runs passed.</div></div>")
    w("<h3>Contents</h3><ol>" + "".join(f"<li>{_e(t)}</li>" for t in SECTION_TITLES) + "</ol>")

    # ---- 1 scope --------------------------------------------------------
    w(f"<h2 class='pb'>1. {SECTION_TITLES[0]}</h2>")
    w(f"<p>{_e(a.get('purpose', ''))}</p>")
    w(_table(["Item", "Value"], [
        ["Product types", _e(", ".join(a.get("product_types", [])))],
        ["Throughput", _e(a.get("throughput_notes") or a.get("throughput_samples_per_run"))],
        ["Regulatory references (method)", _e(", ".join(a.get("regulatory", [])))],
        ["Intended use", _e(m.get("intended_use", "Research use. Automated execution of the method on the named robot, "
                                   "with results reviewed by the analyst."))],
        ["Out of scope", _e(m.get("exclusions", "GMP / GLP / CLIA release testing; 21 CFR Part 11 computer-system "
                                  "validation; assay development; hardware repair."))],
    ]))

    # ---- 2 system -------------------------------------------------------
    w(f"<h2>2. {SECTION_TITLES[1]}</h2>")
    sysrows = [[_e(k), _e(v)] for k, v in m.get("system", {}).items()]
    sysrows += [["Protocol API", _e(proto["requirements"].get("apiLevel"))],
                ["Simulator", _e(ev["versions"]["simulator"])]]
    grav_meta = ev["grav"][0]["result"]["metadata"] if ev["grav"] else {}
    for k in ("instrument", "balance"):
        if grav_meta.get(k):
            sysrows.append([f"Gravimetric {k}", _e(grav_meta[k])])
    w(_table(["Component", "Record"], sysrows))

    # ---- 3 deck ---------------------------------------------------------
    w(f"<h2>3. {SECTION_TITLES[2]}</h2>")
    w(_table(["Deck position"], [[_e(x)] for x in proto["sections"].get("deck", [])]))
    w("<h3>Fill before run (computed by the planner)</h3>")
    w(_table(["Reagent fill"], [[_e(x)] for x in proto["sections"].get("fill", [])]))
    if proto["sections"].get("notes"):
        w("<h3>Operator notes</h3><ul>" + "".join(f"<li>{_e(x)}</li>" for x in proto["sections"]["notes"]) + "</ul>")

    # ---- 4 protocol verification ---------------------------------------
    s = ev["sim"]
    w(f"<h2 class='pb'>4. {SECTION_TITLES[3]}</h2>")
    w("<p>The protocol was generated from the assay library by a planner that checks well and reservoir "
      "capacity (with dead volume), tip-rack usage, pipette range and library-volume drift before any code is "
      "written, and was then executed end to end in the Opentrons simulator. Simulation verifies the code, not "
      "the method; the method is verified in sections 6 and 7.</p>")
    w(_table(["Check", "Result"], [
        ["Simulator exit code", _badge("PASS" if ev["sim_ok"] else "FAIL") + f" {_e(m['protocol'].get('simulator_exit_code'))}"],
        ["Tip pickups / aspirations / dispenses", f"{s['tip_pickups']} / {s['aspirations']} / {s['dispenses']}"],
        ["Operator pauses", str(len(s["pauses"]))],
        ["Timed delays", "; ".join(_e(d.replace('Delaying for ', '')) for d in s["delays"]) or "none"],
        ["Protocol sha256", f"<span class='mono'>{ev['protocol_sha256']}</span>"],
    ]))
    if s["pauses"]:
        w("<h3>Operator pauses, in run order</h3><ol>" + "".join(f"<li>{_e(x)}</li>" for x in s["pauses"]) + "</ol>")

    # ---- 5 criteria -----------------------------------------------------
    w(f"<h2>5. {SECTION_TITLES[4]}</h2>")
    w("<p>Agreed before verification. Plate-level criteria restate the library method's own acceptance "
      "criteria (key shown); liquid-class limits are project-defined by volume band.</p>")
    pc = ev["criteria"].get("plate", {})
    w("<h3>Plate-level criteria (each PQ run)</h3>")
    w(_table(["Library key", "Rule", "Limit", "Source"],
             [[f"<span class='mono'>{_e(c['key'])}</span>", _e(c["type"].replace("_", " ")),
               _e(("≥ " + str(c["min"]) if c.get("min") is not None else "") +
                  (" " if c.get("min") is not None and c.get("max") is not None else "") +
                  ("≤ " + str(c["max"]) if c.get("max") is not None else "") + " " + c.get("unit", "")),
               _e(c.get("source", ""))] for c in pc.get("criteria", [])]))
    w(f"<p class='muted'>Curve model: {_e(pc.get('model'))}; blank subtraction: {_e(pc.get('blank_subtract'))}. "
      f"{_e(pc.get('note', ''))} Run acceptance also requires every well to pass its role rule.</p>")
    gc = ev["grav_config"]
    w("<h3>Liquid-class limits</h3>")
    w(_table(["Target volume up to", "|DEV| max", "CV max"],
             [[f"{b['max_volume_uL']} µL", f"{b['dev_pct']} %", f"{b['cv_pct']} %"] for b in gc["limits"]]))
    w(f"<p class='muted'>Minimum {gc.get('min_replicates', 10)} dispenses per liquid × volume × channel. "
      f"{_e(gc.get('limits_source', ''))}</p>")

    # ---- 6 liquid classes ----------------------------------------------
    w(f"<h2 class='pb'>6. {SECTION_TITLES[5]}</h2>")
    w("<p>Volume = mass / (ρ<sub>liquid</sub> − ρ<sub>air</sub>) × (1 − ρ<sub>air</sub>/ρ<sub>weights</sub>), "
      "with water density from temperature (Tanaka 2001) and declared densities for other liquids (ISO 8655-6 approach).</p>")
    w("<h3>Required liquid classes</h3>")
    w(_table(["Liquid", "Target", "Pipette", "Channels", "Verdict", "Detail"],
             [[_e(l["liquid"]), f"{l['target_uL']:g} µL", _e(l.get("pipette", "")), str(l["channels"]),
               _badge(l["verdict"]), _e(l["detail"])] for l in ev["liquid_status"]]))
    for g in ev["grav"]:
        rows = g["result"]["liquid_classes"]
        w(f"<h3>{_e(os.path.basename(g['path']))} -- {_e(g.get('date', ''))}, {_e(g.get('operator', ''))}</h3>")
        w(_table(["Liquid", "Target", "Pipette", "Ch", "n", "Mean µL", "DEV %", "CV %", "Limit", "Verdict"],
                 [[_e(r["liquid"]), f"{r['target_uL']:g}", _e(r.get("pipette", "")), _e(r["channel"]), str(r["n"]),
                   _e(None if r.get("mean_volume_uL") is None else f"{r['mean_volume_uL']:.2f}"),
                   _e(None if r.get("dev_pct") is None else f"{r['dev_pct']:+.2f}"),
                   _e(None if r.get("cv_pct") is None else f"{r['cv_pct']:.2f}"),
                   f"{_e(r.get('limit_dev_pct'))} / {_e(r.get('limit_cv_pct'))}", _badge(r["verdict"])]
                  for r in rows]))

    # ---- 7 PQ -----------------------------------------------------------
    w(f"<h2 class='pb'>7. {SECTION_TITLES[6]}</h2>")
    w(_table(["Run", "Date", "Operator", "Wells pass", "Plate verdict", "Counts toward acceptance"],
             [[_e(r["run_id"]), _e(r.get("date")), _e(r.get("operator")),
               f"{r['result']['summary']['PASS']}/{sum(r['result']['summary'].values())}",
               _badge(r["plate"]["verdict"]), "yes" if r["passed"] else "no"] for r in ev["runs"]]))
    model = pc.get("model", "linear")
    for r in ev["runs"]:
        pm = json.load(open(os.path.normpath(os.path.join(ev["_base"], r["plate_map"]))))
        plate = r["plate"]
        fit = plate.get("fit") or {}
        w(f"<h3>{_e(r['run_id'])} -- {_e(r.get('date'))} {_badge(plate['verdict'])}</h3>")
        lots = ", ".join(f"{k}: {v}" for k, v in r.get("reagent_lots", {}).items())
        crit_tbl = _table(["Criterion", "Value", "Limit", "Verdict"],
                          [[f"<span class='mono'>{_e(c['key'])}</span>", _e(c["value"]), _e(c["limit"]),
                            _badge(c["verdict"])] for c in plate.get("criteria", [])])
        w(f"<div class='two'><div>{crit_tbl}<p class='muted'>Fit: {_e(fit.get('model'))}, R² {_e(fit.get('r2'))}, "
          f"{_e(fit.get('n_points'))} points. Blank mean {_e(plate.get('blank_mean'))}."
          f"{' Reagent lots: ' + _e(lots) if lots else ''}</p></div>"
          f"<div>{curve_svg(r, pm, model)}</div></div>")
        samples = plate.get("samples", [])
        if samples:
            w(_table(["Group", "Role", "Wells", "Mean signal", "Concentration", "CV %", "In range"],
                     [[_e(x["group"]), _e(x["role"]), _e(" ".join(x["wells"])), _e(x["mean_signal"]),
                       f"{_e(x['concentration'])} {_e(x['unit'])}", _e(x["cv_pct"]),
                       "yes" if x["within_standard_range"] else "<b>no</b>"] for x in samples], cls="small"))

    # ---- 8 deviations ---------------------------------------------------
    w(f"<h2 class='pb'>8. {SECTION_TITLES[7]}</h2>")
    w("<p>Generated from the evidence: every criterion that did not pass, every flag raised during scoring, "
      "and every planned deviation from the library method recorded by the protocol generator.</p>")
    w(_table(["#", "Type", "Where", "Description", "Disposition"],
             [[str(i + 1), _e(d["kind"]), _e(d["where"]), _e(d["text"]), ""]
              for i, d in enumerate(ev["deviations"])]) if ev["deviations"] else "<p>None.</p>")

    # ---- 9 limitations --------------------------------------------------
    w(f"<h2>9. {SECTION_TITLES[8]}</h2><ul>")
    lim = [
        "Research use only. This pack is not GMP/GLP/CLIA validation and not 21 CFR Part 11 computer-system validation; the OT-2 is not Part 11 compliant.",
        "Liquid-class results apply to the liquids, volumes, pipettes and tips listed in section 6 only.",
        "Residual volume after aspiration is not modelled by the planner; it is controlled by the aspirate height set in the protocol and confirmed by the PQ runs.",
        "Assay performance is demonstrated only for the sample types, concentration range and reagent lots listed in section 7.",
    ] + m.get("limitations", [])
    w("".join(f"<li>{_e(x)}</li>" for x in lim) + "</ul>")

    # ---- 10 change control ---------------------------------------------
    w(f"<h2>10. {SECTION_TITLES[9]}</h2>")
    w(_table(["Change", "Re-verification required"], [
        ["New reagent lot (critical reagents)", "One PQ run against current lot results"],
        ["New kit, liquid or labware type", "Liquid-class verification (section 6) and 3 PQ runs"],
        ["Protocol change (any regenerated file with a new sha256)", "Simulation (section 4) and 1 PQ run; 3 if a volume or step changed"],
        ["Pipette replaced or serviced", "Liquid-class verification for that pipette"],
        ["Robot software / firmware update", "Simulation and 1 PQ run"],
        ["Acceptance-criteria change", "Re-score retained PQ data; document the rationale"],
    ]))

    # ---- 11 traceability ------------------------------------------------
    w(f"<h2 class='pb'>11. {SECTION_TITLES[10]}</h2>")
    w("<p>Every input is identified by SHA-256. Re-running the assembler on the same files reproduces every "
      "number in this pack; each scoring run is also recorded in the Results Bridge audit log.</p>")
    w(_table(["Input", "File", "SHA-256"], [[_e(t["what"]), _e(t["file"]), f"<span class='mono'>{t['sha256']}</span>"]
                                           for t in ev["trace"]]))
    w(_table(["Software", "Version"], [[_e(k), _e(v)] for k, v in ev["versions"].items()]))

    # ---- 12 sign-off ----------------------------------------------------
    w(f"<h2>12. {SECTION_TITLES[11]}</h2><h3>Training record</h3>")
    w(_table(["Operator", "Topics", "Supervised solo run (run ID)", "Date", "Signature"],
             [[_e(t.get("name", "")), _e(t.get("topics", "")), _e(t.get("solo_run", "")), _e(t.get("date", "")), ""]
              for t in m.get("training", [])] or [["", "", "", "", ""]] * 3, cls="sig"))
    w("<h3>Approval</h3>")
    w(_table(["Role", "Name", "Signature", "Date"],
             [["Prepared by (implementer)", _e(m.get("prepared_by", "")), "", ""],
              ["Reviewed by (client method owner)", "", "", ""],
              ["Approved by (client lab head)", "", "", ""]], cls="sig"))
    w(f"<p class='muted'>Status at signature: {_e(ev['status'])}. Protocol version accepted: "
      f"<span class='mono'>{ev['protocol_sha256']}</span></p>")
    w("</body></html>")
    return "".join(out)


def build_pack(manifest_path: str, out_html: str, out_pdf: str | None = None) -> dict:
    base = os.path.dirname(os.path.abspath(manifest_path))
    manifest = json.load(open(manifest_path))
    ev = evaluate(manifest, base)
    ev["_base"] = base
    doc = render(ev)
    with open(out_html, "w") as fh:
        fh.write(doc)
    pdf_made = False
    if out_pdf:
        from weasyprint import HTML  # optional dependency
        HTML(string=doc, base_url=base).write_pdf(out_pdf)
        pdf_made = True
    return {"pack_id": manifest.get("pack_id"), "status": ev["status"], "accepted": ev["accepted"],
            "reasons": ev["reasons"], "html": out_html, "pdf": out_pdf if pdf_made else None,
            "deviations": len(ev["deviations"]), "runs": [(r["run_id"], r["plate"]["verdict"]) for r in ev["runs"]],
            "liquid_classes": [(l["liquid"], l["target_uL"], l.get("pipette"), l["verdict"]) for l in ev["liquid_status"]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Assemble a validation pack from a manifest.")
    ap.add_argument("manifest")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument("--pdf", help="also write a PDF (needs weasyprint)")
    args = ap.parse_args(argv)
    summary = build_pack(args.manifest, args.out, args.pdf)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
