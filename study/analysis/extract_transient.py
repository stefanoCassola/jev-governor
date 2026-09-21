"""Compact histories of selected transient runs: outer iterations per step and the factor paths.
usage: extract_transient.py <runs_root> <out.json> <group/run> [...]"""
import csv
import json
import sys
from pathlib import Path

root, outp = Path(sys.argv[1]), Path(sys.argv[2])
res = {}
for r in sys.argv[3:]:
    d = root / r
    rows = list(csv.DictReader(open(d / "steps.csv")))
    rec = {"n_outer": [int(x["n_outer"]) for x in rows], "converged": [int(x["converged"]) for x in rows],
           "result": {k: v for k, v in json.loads((d / "result.json").read_text()).items()
                      if k in ("completed", "steps", "outer_total", "unconverged_steps", "init", "final_factors", "method", "async")}}
    dec = d / "decisions.jsonl"
    if dec.exists() and dec.stat().st_size:
        rec["decisions"] = [{"step": x["iteration"], "U": x["to"]["U"], "p": x["to"]["p"], "move": x.get("move"),
                             "guard": bool(x.get("guard"))} for x in map(json.loads, dec.read_text().splitlines())]
    res[r] = rec
outp.write_text(json.dumps(res)); print(outp, list(res))
