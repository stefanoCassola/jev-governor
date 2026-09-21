"""Extract compact residual histories and factor paths of selected runs to JSON (for figures).

usage: extract_hist.py <out.json> <run_dir> [<run_dir> ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog2 import FoamLog  # noqa: E402

out = {}
for d in sys.argv[2:]:
    d = Path(d)
    L = FoamLog(d / "log.simpleFoam"); L.poll()
    rec = {"it": [i.it for i in L.iters], "res": {}}
    for f in L.iters[-1].res if L.iters else []:
        rec["res"][f] = [i.res.get(f) for i in L.iters]
    dec = d / "decisions.jsonl"
    if dec.exists():
        rec["path"] = [(r["iteration"], r["to"]["U"], r["to"]["p"], r.get("unstable"))
                       for r in map(json.loads, dec.read_text().splitlines())]
    res = json.loads((d / "result.json").read_text())
    rec["init"] = res.get("init")
    out[f"{d.parent.name}/{d.name}"] = rec
Path(sys.argv[1]).write_text(json.dumps(out))
print(len(out), "runs")
