"""Residual floor per field: median of the initial residual over the last 2000 iterations and its min.
usage: floors.py <run_dir> ..."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog2 import FoamLog
import statistics as st
for d in sys.argv[1:]:
    L = FoamLog(Path(d) / "log.simpleFoam"); L.poll()
    it = L.iters
    out = []
    for f in it[-1].res:
        v = [i.res[f] for i in it[-2000:] if f in i.res]
        w = [i.res[f] for i in it if f in i.res]
        k = next((i.it for i in it if f in i.res and i.res[f] < 10 * min(v)), None)
        out.append(f"{f}: med={st.median(v):.2e} min={min(v):.2e} first<10xmin@{k}")
    print(Path(d).name, len(it), " | ".join(out))
