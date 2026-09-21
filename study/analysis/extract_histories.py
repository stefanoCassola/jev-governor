"""Extract compact per-iteration histories (residuals, factor paths, permeability) from raw logs to JSON.

usage: extract_histories.py bench <raw_bench_runs_dir> <out.json> <group/run> [...]
       extract_histories.py perm  <raw_perm_runs_dir>  <out.json> <run> [...]
The raw logs are not part of the repository (about 1 GB); the JSON written here is.
"""
import json
import re
import sys
from pathlib import Path

RES = re.compile(r"Solving for (\w+), Initial residual = ([\d.eE+-]+|nan|inf)")


def bench(run: Path) -> dict:
    its, res, cur = [], {}, None
    for line in (run / "log.simpleFoam").read_text(errors="replace").splitlines():
        if line.startswith("Time = "):
            cur = {}; its.append(int(float(line[7:]))); res_now = cur
            for k in res:
                res[k].append(None)
        elif cur is not None:
            m = RES.search(line)
            if m:
                f = "U" if m.group(1) in ("Ux", "Uy", "Uz") else m.group(1)
                v = float(m.group(2))
                res.setdefault(f, [None] * len(its))
                old = res[f][-1]
                if f == "U":
                    res[f][-1] = v if old is None else max(old, v)      # residualControl tests the largest component
                elif old is None:
                    res[f][-1] = v                                      # first solve of the iteration
    out = {"it": its, "res": res, "targets": json.loads((run / "case.json").read_text())["targets"],
           "result": {k: v for k, v in json.loads((run / "result.json").read_text()).items()
                      if k in ("converged", "fatal", "iterations", "method", "init", "final_factors", "async_applied_at")}}
    dec = run / "decisions.jsonl"
    if dec.exists():
        out["decisions"] = [{"it": r["iteration"], "U": r["to"]["U"], "p": r["to"]["p"], "move": r.get("move"),
                             "probs": r.get("probs"), "guard": r.get("guard"), "ceiling": r.get("ceiling")}
                            for r in map(json.loads, dec.read_text().splitlines()) if r.get("to")]
    return out


def perm(run: Path) -> dict:
    its, K, slope, err, Ux = [], [], [], [], []
    cur = None; want = None
    for line in (run / "log.simpleFoamMod").read_text(errors="replace").splitlines():
        if line.startswith("Time = "):
            cur = int(line[7:]); its.append(cur); K.append(None); slope.append(None); err.append(None); Ux.append(None)
        elif cur is None:
            continue
        elif "Main flow direction:" in line:
            K[-1] = float(line.split(":")[1].split()[0])
        elif line.startswith("Normalized slope"):
            slope[-1] = float(line.split(":")[1])
        elif line.startswith("Error predicted"):
            err[-1] = float(line.split(":")[1])
        else:
            m = RES.search(line)
            if m and m.group(1) == "Ux" and Ux[-1] is None:
                Ux[-1] = float(m.group(2))
    out = {"it": its, "K": K, "slope": slope, "pred_err": err, "res_Ux": Ux}
    dec = run / "jev_decisions.jsonl"
    if dec.exists():
        out["events"] = [{k: r.get(k) for k in ("iteration", "kind", "from", "to", "unstable", "conv", "reason") if k in r}
                         for r in map(json.loads, dec.read_text().splitlines())]
    return out


mode, root, outp = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
fn = bench if mode == "bench" else perm
res = {r: fn(root / r) for r in sys.argv[4:]}
outp.parent.mkdir(parents=True, exist_ok=True); outp.write_text(json.dumps(res))
print(outp, {k: len(v["it"]) for k, v in res.items()})
