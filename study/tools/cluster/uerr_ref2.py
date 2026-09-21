"""Velocity error of every pitzDaily / Pawar-Maulik run against the new long references (runs/ref2).
usage: uerr_ref2.py <out.csv>   (run inside bench/)"""
import csv, re, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, 'python')
from collect import read_U  # noqa: E402
R = Path('runs'); cache = {}
def ref(t, tag):
    k = (t, tag)
    if k not in cache:
        p = R / ('ref' if tag == 'ref' else 'ref2') / f'{t}__{tag}'
        cache[k] = read_U(p) if p.exists() else None
    return cache[k]
rows = []
for g in ['main', 'v3', 'v3held', 'v4', 'v4held2', 'v4a', 'v4aheld2', 'ref', 'ref2']:
    for d in sorted((R / g).iterdir()):
        t = d.name.split('__')[0]
        if not (t.startswith('pm_bfs') or t.startswith('pitzDaily')) or not (d / 'result.json').exists():
            continue
        u, ur = read_U(d), ref(t, 'ref2')
        if u is None or ur is None or u.shape != ur.shape:
            continue
        row = {'group': g, 'run': d.name, 'template': t, 'u_err_ref2': float(np.linalg.norm(u - ur) / np.linalg.norm(ur))}
        urb = ref(t, 'ref2b')
        if urb is not None:
            row['u_err_ref2b'] = float(np.linalg.norm(u - urb) / np.linalg.norm(urb))
        rows.append(row)
with open(sys.argv[1], 'w', newline='') as f:
    w = csv.DictWriter(f, ['group', 'run', 'template', 'u_err_ref2', 'u_err_ref2b']); w.writeheader(); w.writerows(rows)
print(len(rows))
