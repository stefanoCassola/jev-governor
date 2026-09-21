"""Run one transient pimpleFoam case with fixed or adaptive PIMPLE outer-loop under-relaxation.

Methods: static (case defaults or --relax U p), heurT (rule-based twin), jevT (judgment model).
Adaptive methods decide every --interval TIME STEPS. Synchronous hand-off (default): the solver blocks at the
end of every interval-th step until the new factors are written (jevSync function object), so iteration
counts do not depend on the latency of the model. --async: the model is called in a background thread and its
answer is applied at the first hand-off after it arrives; the code-only guard stays synchronous.

case.json of the template: {"solver": "pimpleFoam", "description", "context", "U", "p", "turb", "turb_fields",
  "turb_follow", "targets": {field: outer-loop tolerance}, "cap": nOuterCorrectors, "p_rule", "floor"}
usage: pimplectl.py <template_case> <run_dir> --method static|heurT|jevT [--relax U p] [--interval 10] [--async]
Writes <run_dir>/result.json, decisions.jsonl and steps.csv (outer iterations per time step).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog_t import PimpleLog  # noqa: E402

SIF = "/work/cassola/openfoam/openfoam2312Mod.sif"
ROOT = Path(__file__).resolve().parents[2]          # /scratch/cassola/open_jev
BOUNDS = {"U": (0.3, 0.95), "p": (0.1, 0.9)}


def write_relax(case: Path, f: dict, prof: dict) -> None:
    """Replace the relaxationFactors block; the final outer iteration of each step stays unrelaxed."""
    path = case / "system/fvSolution"
    s = path.read_text()
    i = s.find("relaxationFactors")
    if i >= 0:
        j = s.index("{", i); d = 0
        for k in range(j, len(s)):
            d += s[k] == "{"; d -= s[k] == "}"
            if d == 0:
                break
        s = s[:i] + s[k + 1:]
    eq = f"        U               {f['U']};\n        UFinal          1;\n"
    if f.get("turb") is not None:
        tf = "|".join(prof["turb_fields"])
        eq += f'        "({tf})"      {f["turb"]};\n        "({tf})Final" 1;\n'
    block = ("relaxationFactors\n{\n    fields\n    {\n        p               " + f"{f['p']};\n        pFinal          1;\n    }}\n"
             "    equations\n    {\n" + eq + "    }\n}\n")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(s.rstrip() + "\n\n" + block)
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("template", type=Path)
    ap.add_argument("run", type=Path)
    ap.add_argument("--method", choices=["static", "heurT", "jevT"], required=True)
    ap.add_argument("--relax", type=float, nargs="+", help="initial U p [turb]")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--model", default="jev-1.13.0")
    ap.add_argument("--async", dest="async_", action="store_true")
    ap.add_argument("--tight", type=float, default=0, help="multiply the outer-loop tolerances by this")
    ap.add_argument("--cap", type=int, default=0, help="override nOuterCorrectors")
    ap.add_argument("--steps", type=int, default=0, help="override the number of time steps")
    a = ap.parse_args()

    tpl, run = a.template.resolve(), a.run.resolve()
    if run.exists():
        shutil.rmtree(run)
    shutil.copytree(tpl, run, symlinks=True, ignore=shutil.ignore_patterns("constant"))
    (run / "constant").mkdir()
    for p in (tpl / "constant").iterdir():
        (run / "constant" / p.name).symlink_to(p) if p.name == "polyMesh" else \
            (shutil.copytree(p, run / "constant" / p.name) if p.is_dir() else shutil.copy(p, run / "constant"))
    prof = json.loads((tpl / "case.json").read_text())
    fv = run / "system/fvSolution"
    if a.tight:
        txt = fv.read_text()
        i = txt.index("residualControl"); j = txt.index("}\n    }", i)
        blk = re.sub(r"(tolerance\s+)([0-9.eE+-]+)", lambda m: f"{m.group(1)}{float(m.group(2)) * a.tight:g}", txt[i:j])
        fv.write_text(txt[:i] + blk + txt[j:])
        prof["targets"] = {k: v * a.tight for k, v in prof["targets"].items()}
    if a.cap:
        fv.write_text(re.sub(r"nOuterCorrectors\s+\d+;", f"nOuterCorrectors {a.cap};", fv.read_text()))
        prof["cap"] = a.cap
    cd = run / "system/controlDict"
    txt = cd.read_text()
    dt = float(re.search(r"^deltaT\s+([0-9.eE+-]+);", txt, re.M).group(1))
    t0 = float(re.search(r"^startTime\s+([0-9.eE+-]+);", txt, re.M).group(1))
    end = float(re.search(r"^endTime\s+([0-9.eE+-]+);", txt, re.M).group(1))
    nsteps = a.steps or round((end - t0) / dt)
    txt = re.sub(r"^endTime\s+.*$", f"endTime         {t0 + nsteps * dt:.10g};", txt, flags=re.M)
    txt = re.sub(r"^writeControl\s+.*$", "writeControl    timeStep;", txt, flags=re.M)
    txt = re.sub(r"^writeInterval\s+.*$", f"writeInterval   {nsteps};", txt, flags=re.M)
    cd.write_text(txt)

    f = {"U": prof["U"], "p": prof["p"], "turb": prof.get("turb")}
    if a.relax:
        f["U"], f["p"] = a.relax[0], a.relax[1]
        if f["turb"] is not None:
            f["turb"] = a.relax[2] if len(a.relax) > 2 else (a.relax[0] if prof.get("turb_follow", True) else prof["turb"])
    write_relax(run, f, prof)
    adaptive = a.method != "static"
    (run / "sync").mkdir(exist_ok=True)
    (run / "sync/interval").write_text(f"{1 if adaptive else 0}\n")   # hand-off after every step

    ctl = None
    if adaptive:
        import ctlt
        if a.method == "jevT":
            if not os.environ.get("TYPESAFE_API_KEY"):
                os.environ["TYPESAFE_API_KEY"] = (ROOT / "jev_api_key").read_text().strip()
            judge = ctlt.JevT(a.model)
        else:
            judge = ctlt.HeurT()
        ctl = ctlt.ControllerT(judge, prof, BOUNDS, a.interval)

    logp = run / "log.pimpleFoam"
    t_start = time.time()
    with open(logp, "w") as lf:
        proc = subprocess.Popen(["stdbuf", "-oL", "-eL", "apptainer", "exec", "--bind",
                                 ",".join(sorted({"/scratch", "/work", "/" + run.parts[1]})), SIF,
                                 "openfoam2312", prof.get("solver", "pimpleFoam"), "-case", str(run)],
                                stdout=lf, stderr=subprocess.STDOUT)
    log = PimpleLog(logp)
    dec = open(run / "decisions.jsonl", "w")
    hist: list[dict] = []
    wait_total, n_dec, n_calls = 0.0, 0, 0

    def apply(n: int, nf: dict, extra: dict) -> None:
        nonlocal f, n_dec, n_calls
        rec = {"iteration": n, "from": dict(f), "to": nf, **extra["info"]}
        if (nf["U"], nf["p"]) != (f["U"], f["p"]):
            write_relax(run, nf, prof)
            f = nf
        dec.write(json.dumps(rec) + "\n"); dec.flush()
        hist.append(extra["hist"])
        n_dec += 1
        n_calls += 1 if "latency_s" in rec else 0

    pending: dict | None = None      # --async: a judgment that is being computed in the background

    def on_step(n: int, steps: list, due: bool) -> None:
        """Called at the hand-off after EVERY time step (the solver waits only for this function).
        1. code-only guard; 2. apply a finished background judgment; 3. start a decision when one is due.
        Synchronous mode waits for the model inside step 3; --async starts a thread and returns at once, so the
        solver never waits for the model, while the guard stays synchronous."""
        nonlocal pending
        g = ctl.step_guard(steps, n, f, hist)
        if g is not None:
            apply(n, *g)
            if pending is not None:
                pending["stale"] = True          # the state it was based on is gone
            return
        if pending is not None and not pending["thread"].is_alive():
            pd, pending = pending, None
            if not pd["stale"]:
                nf, extra = ctl.finish(pd["feats"], pd["out"][0], pd["out"][1], n, f, hist)
                extra["info"]["decided_at"], extra["info"]["applied_at"] = pd["n"], n
                apply(n, nf, extra)
        if due and pending is None:
            if not a.async_:
                apply(n, *ctl.decide(steps, n, f, hist))
            else:
                feats = ctl.prepare(steps, n, f)
                pd = {"n": n, "feats": feats, "stale": False, "out": None}
                fs, hs = dict(f), list(hist)

                def work():
                    pd["out"] = ctl.judge_call(feats, fs, hs)
                pd["thread"] = threading.Thread(target=work, daemon=True)
                pd["thread"].start()
                pending = pd

    if not adaptive:
        rc = proc.wait()
    else:
        served = set()
        while True:
            rc = proc.poll()
            waiting = [n for n in sorted(int(p.name[5:]) for p in (run / "sync").glob("wait_*")) if n not in served]
            if not waiting:
                if rc is not None:
                    break
                time.sleep(0.003)
                continue
            n = waiting[0]
            tw = time.time()
            log.complete_through(n)
            st = log.upto(n)
            if st and n < nsteps:
                on_step(n, st, n % a.interval == 0)
            (run / "sync" / f"go_{n}").touch()
            (run / "sync" / f"wait_{n}").unlink(missing_ok=True)
            served.add(n)
            wait_total += time.time() - tw
    log.poll()
    st = log.steps
    with open(run / "steps.csv", "w") as fh:
        fh.write("step,t,n_outer,converged,co_max,lin_p,exec_s\n")
        for s in st:
            fh.write(f"{s.idx},{s.t:.10g},{s.n_outer},{int(s.converged)},{s.co_max},{s.lin},{s.exec_s}\n")
    done = len(st) >= nsteps and log.ended and not log.fatal and rc == 0
    result = {
        "template": tpl.name, "run": run.name, "method": a.method, "interval": a.interval if adaptive else 0,
        "init": a.relax, "final_factors": f, "rc": rc, "completed": done, "fatal": log.fatal or rc != 0,
        "steps": len(st), "steps_planned": nsteps, "outer_total": sum(s.n_outer for s in st),
        "unconverged_steps": sum(1 for s in st if not s.converged), "outer_max": max((s.n_outer for s in st), default=0),
        "lin_p_total": sum(s.lin for s in st), "co_max": max((s.co_max or 0 for s in st), default=None),
        "exec_s": st[-1].exec_s if st else None, "clock_s": st[-1].clock_s if st else None,
        "wall_s": round(time.time() - t_start, 2), "controller_wait_s": round(wait_total, 2),
        "decisions": n_dec, "model_calls": n_calls, "host": os.uname().nodename, "async": a.async_,
        "async_applied_at": log.async_applied, "tight": a.tight, "cap": prof["cap"],
    }
    (run / "result.json").write_text(json.dumps(result, indent=1))
    shutil.rmtree(run / "sync", ignore_errors=True)
    shutil.rmtree(run / "dynamicCode", ignore_errors=True)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
