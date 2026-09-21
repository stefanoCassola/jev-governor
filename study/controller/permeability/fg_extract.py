"""Extract FiberGeo run histories (permeability, stop criteria, time) and relaxation paths to JSON.

usage: fg_extract.py <out.json> <run_dir> [<run_dir> ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog import parse_full  # noqa: E402

out = {}
for d in map(Path, sys.argv[2:]):
    L = parse_full(d / "log.simpleFoamMod")
    it = L.iters
    rec = {"it": [i.it for i in it], "K": [i.perm for i in it], "slope": [i.slope for i in it],
           "err": [i.err for i in it], "exec": [i.exec_s for i in it],
           "resUx": [i.res.get("Ux") for i in it], "resp": [i.res.get("p") for i in it],
           "converged": L.converged, "fatal": L.fatal,
           "done": (d / "end_epoch").exists()}
    dec = d / "jev_decisions.jsonl"
    if dec.exists():
        recs = [json.loads(x) for x in dec.read_text().splitlines() if x.strip()]
        rec["path"] = [(r["iteration"], r["to"][0], r["to"][1]) for r in recs
                       if r.get("kind") in ("decision", "guard") and "to" in r]
        rec["calls"] = sum(1 for r in recs if r.get("kind") == "decision")
        rec["guards"] = sum(1 for r in recs if r.get("kind") == "guard")
    info = d / "run_info"
    rec["info"] = info.read_text() if info.exists() else ""
    src = d / "constant" / ".." / "source"
    out[d.name] = rec
Path(sys.argv[1]).write_text(json.dumps(out))
print(len(out), "runs")
