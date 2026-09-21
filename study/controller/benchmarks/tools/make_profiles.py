"""Write case.json (controller profile) into every benchmark template.

Defaults and residual targets are read from the template's system/fvSolution, so a profile
always matches its case. usage: make_profiles.py <cases_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DESC = {
    "cavity": ("laminar", "two-dimensional lid-driven square cavity at Reynolds number {re}, uniform 128x128 mesh"),
    "lbfs": ("laminar", "two-dimensional laminar flow over a backward-facing step (expansion ratio 2) at "
                        "Reynolds number {re}, parabolic inflow"),
    "pitzDaily": ("turbulent", "two-dimensional turbulent flow over a backward-facing step with a "
                               "contraction (pitzDaily), RANS k-epsilon model, inlet velocity {uin} m/s"),
    "bfs2d": ("turbulent", "two-dimensional turbulent flow over a backward-facing step (Driver and "
                           "Seegmiller), RANS k-omega SST model, wall-resolved mesh"),
    "pm": ("turbulent", "two-dimensional turbulent flow over a backward-facing step (Driver and Seegmiller "
                        "geometry, step height 12.7 mm), RANS k-omega SST model, inlet velocity {uin} m/s"),
    "airFoil2D": ("turbulent", "two-dimensional turbulent flow around an airfoil, RANS Spalart-Allmaras "
                               "model, freestream velocity {uin} m/s at {aoa} degrees angle of attack"),
}
TURB = {"pitzDaily": ["k", "epsilon"], "bfs2d": ["k", "omega"], "airFoil2D": ["nuTilda"], "pm": ["k", "omega"]}


def num(s: str, key: str, block: str) -> float | None:
    b = re.search(block + r"\s*\{([^{}]*)\}", s, re.S)
    if not b:
        return None
    m = re.search(r'(?:^|\s)"?' + re.escape(key) + r'"?\s+([0-9.eE+-]+)\s*;', b.group(1))
    return float(m.group(1)) if m else None


def profile(case: Path) -> dict:
    base = case.name.split("_")[0]
    s = (case / "system/fvSolution").read_text()
    s_nc = re.sub(r"//.*", "", s)
    algo = "SIMPLEC" if re.search(r"consistent\s+(yes|true|on)\s*;", s_nc) else "SIMPLE"
    rc = re.search(r"residualControl\s*\{([^{}]*)\}", s_nc, re.S).group(1)
    targets = {}
    for m in re.finditer(r'"?([\w|()]+)"?\s+([0-9.eE+-]+)\s*;', rc):
        k, v = m.group(1), float(m.group(2))
        if k.startswith("("):
            for t in TURB.get(base, []):
                if t in k:
                    targets[t] = v
        else:
            targets[k] = v
    if base in TURB:
        for t in TURB[base]:
            targets.setdefault(t, targets.get("U"))
    u = num(s_nc, "U", "equations")
    p = num(s_nc, "p", "fields")
    turb = None
    if base in TURB:
        turb = num(s_nc, TURB[base][0], "equations") or num(s_nc, ".*", "equations") or u
    regime, desc = DESC[base]
    re_ = re.search(r"Re(\d+)", case.name)
    uin = re.search(r"_U(\d+)", case.name)
    aoa = re.search(r"_a(-?\d+)", case.name)
    desc = desc.format(re=re_.group(1) if re_ else "", uin=uin.group(1) if uin else ("26" if base == "airFoil2D" else "10"),
                       aoa=aoa.group(1) if aoa else "8")
    algo_note = ("SIMPLEC (consistent) pressure-velocity coupling; with SIMPLEC, pressure "
                 "under-relaxation factors close to 1 are usually tolerated" if algo == "SIMPLEC" else
                 "standard SIMPLE pressure-velocity coupling; with SIMPLE the pressure under-relaxation "
                 "factor is usually kept well below 1, roughly one minus the momentum factor")
    context = (f"Steady incompressible {regime} flow: {desc}. Solved with OpenFOAM simpleFoam using "
               f"{algo_note}. The run stops once every equation residual is below its convergence target. "
               "Goal: reach convergence in as few iterations as possible without oscillation or divergence. "
               "The momentum (U) under-relaxation factor acts like a pseudo time step: larger values give "
               "faster progress but less stability." +
               (" The turbulence equations' factor is held fixed." if base == "pm" else
                " The turbulence equations' factor is moved together with the momentum factor." if turb else ""))
    return {"solver": "simpleFoam", "algorithm": algo, "regime": regime, "description": desc,
            "U": u, "p": p if p is not None else 1.0, "turb": turb, "turb_fields": TURB.get(base, []),
            "targets": targets, "monitor_re": None, "context": context, "turb_follow": base != "pm"}


if __name__ == "__main__":
    for c in sorted(Path(sys.argv[1]).iterdir()):
        if (c / "system/fvSolution").exists():
            prof = profile(c)
            (c / "case.json").write_text(json.dumps(prof, indent=1))
            print(c.name, prof["algorithm"], "U", prof["U"], "p", prof["p"], "turb", prof["turb"], prof["targets"])
