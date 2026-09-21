"""Run one transient benchmark condition with jevGovernor (PIMPLE mode) on the cluster.

usage: govbench.py <template_case> <run_dir> --backend rules|jev [--async]
The solver runs inside the Apptainer image; the sidecar is started outside it from this venv (launchSidecar no).
Writes <run_dir>/result.json in the format of pimplectl.py / collect_t.py plus the governor's own counts.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SIF = "/work/cassola/openfoam/openfoam2312Mod.sif"
ROOT = Path(__file__).resolve().parents[2]
RE_EXEC = re.compile(r"^ExecutionTime = ([\d.]+) s\s+ClockTime = ([\d.]+) s", re.M)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("template", type=Path); ap.add_argument("run", type=Path)
    ap.add_argument("--backend", choices=["rules", "jev"], required=True)
    ap.add_argument("--async", dest="async_", action="store_true")
    a = ap.parse_args()
    tpl, run = a.template.resolve(), a.run.resolve()
    if run.exists():
        shutil.rmtree(run)
    shutil.copytree(tpl, run, symlinks=True, ignore=shutil.ignore_patterns("constant", "dynamicCode"))
    (run / "constant").mkdir()
    for p in (tpl / "constant").iterdir():
        (run / "constant" / p.name).symlink_to(p) if p.name == "polyMesh" else \
            (shutil.copytree(p, run / "constant" / p.name) if p.is_dir() else shutil.copy(p, run / "constant"))
    prof = json.loads((tpl / "case.json").read_text())
    cd = run / "system/controlDict"
    txt = cd.read_text()
    dt = float(re.search(r"^deltaT\s+([0-9.eE+-]+);", txt, re.M).group(1))
    end = float(re.search(r"^endTime\s+([0-9.eE+-]+);", txt, re.M).group(1))
    nsteps = round(end / dt)
    txt = txt[:txt.index("\nfunctions")]
    txt = re.sub(r"^writeControl\s+.*$", "writeControl    timeStep;", txt, flags=re.M)
    txt = re.sub(r"^writeInterval\s+.*$", f"writeInterval   {nsteps};", txt, flags=re.M)
    desc = prof["context"].replace('"', "'")
    txt += f"""
functions
{{
    jevGovernor
    {{
        type            jevGovernor;
        libs            (jevGovernor);
        algorithm       PIMPLE;
        interval        10;
        mode            {'async' if a.async_ else 'sync'};
        backend         {a.backend};
        launchSidecar   no;
        momentum        U;
        pressure        p;
        bounds          (0.3 0.95);
        pressureBounds  (0.1 0.9);
        floor           3;
        checkpoint      no;
        description     "{desc}";
    }}
}}
"""
    cd.write_text(txt)
    env = dict(os.environ)
    if a.backend == "jev" and not env.get("TYPESAFE_API_KEY"):
        env["TYPESAFE_API_KEY"] = (ROOT / "jev_api_key").read_text().strip()
    t0 = time.time()
    side = subprocess.Popen([str(Path(sys.executable).parent / "jev-governor"), "serve", "--case", str(run),
                             "--idle-timeout", "600", "--backend", a.backend],
                            stdout=open(run / "log.sidecar", "w"), stderr=subprocess.STDOUT, env=env)
    with open(run / "log.pimpleFoam", "w") as lf:
        rc = subprocess.call(["stdbuf", "-oL", "-eL", "apptainer", "exec", "--bind",
                              ",".join(sorted({"/scratch", "/work", "/" + run.parts[1]})), SIF, "openfoam2312",
                              "pimpleFoam", "-case", str(run)], stdout=lf, stderr=subprocess.STDOUT)
    wall = time.time() - t0
    try:
        side.wait(timeout=90)
    except subprocess.TimeoutExpired:
        side.kill()
    g = run / "jevGovernor"
    summ = json.loads((g / "summary.json").read_text()) if (g / "summary.json").exists() else {}
    steps = list(csv.DictReader(open(g / "steps.csv"))) if (g / "steps.csv").exists() else []
    log = (run / "log.pimpleFoam").read_text(errors="replace")
    ex = RE_EXEC.findall(log)
    ended = bool(re.search(r"^End", log, re.M)) and rc == 0
    dec = [json.loads(x) for x in (g / "decisions.jsonl").read_text().splitlines() if x.strip()] if (g / "decisions.jsonl").exists() else []
    lat = [d.get("latency_s") or d.get("latency") or 0 for d in dec]
    slow = sum(1 for s in steps if s["class"] == "slow_capped"); unst = sum(1 for s in steps if s["class"] == "unstable")
    result = {"template": tpl.name, "run": run.name, "method": "gov_" + a.backend, "interval": 10, "init": None,
              "final_factors": {"U": float(steps[-1]["aU"]), "p": float(steps[-1]["ap"])} if steps else {},
              "rc": rc, "completed": ended and len(steps) >= nsteps, "fatal": rc != 0 or "FOAM FATAL" in log,
              "steps": len(steps), "steps_planned": nsteps, "outer_total": sum(int(s["n_outer"]) for s in steps),
              "unconverged_steps": slow + unst, "slow_capped_steps": slow, "unstable_steps": unst,
              "outer_max": max((int(s["n_outer"]) for s in steps), default=0),
              "exec_s": float(ex[-1][0]) if ex else None, "clock_s": float(ex[-1][1]) if ex else None,
              "wall_s": round(wall, 2), "api_latency_s": round(sum(lat), 2), "decisions": len(dec),
              "model_calls": sum(1 for x in lat if x), "host": os.uname().nodename, "async": a.async_,
              "governor_summary": summ, "cap": prof["cap"]}
    (run / "result.json").write_text(json.dumps(result, indent=1))
    for t in run.iterdir():       # keep only the final time directory's U for the accuracy check
        if t.is_dir() and re.fullmatch(r"[0-9.eE+-]+", t.name) and t.name != "0":
            for f in t.iterdir():
                if f.name != "U":
                    shutil.rmtree(f) if f.is_dir() else f.unlink()
    print(json.dumps({k: result[k] for k in ("run", "completed", "steps", "outer_total", "slow_capped_steps", "unstable_steps")}))


if __name__ == "__main__":
    main()
