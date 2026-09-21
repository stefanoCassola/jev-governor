"""Relative L2 difference of the final velocity fields of the permeability runs against the default run.
usage: fg_fielddiff.py <runs_dir> <out.csv> <case> [...]"""
import re, sys
from pathlib import Path
import numpy as np


def read_U(run):
    ts = [p for p in Path(run).iterdir() if p.is_dir() and re.fullmatch(r'\d+', p.name) and p.name != '0']
    d = max(ts, key=lambda p: int(p.name))
    s = (d / 'U').read_bytes()
    m = re.search(rb'internalField\s+nonuniform\s+List<vector>\s*(\d+)\s*\(', s)
    n = int(m.group(1)); body = s[m.end():s.find(b'\n)\n', m.end())].replace(b'(', b' ').replace(b')', b' ')
    return np.array(body.split(), dtype=np.float64).reshape(n, 3), int(d.name)


runs = Path(sys.argv[1]); out = open(sys.argv[2], 'a')
for c in sys.argv[3:]:
    pre = 'base' if c == 'base' else f'fg_{c}'
    names = {'default': 'base_ref' if c == 'base' else f'{pre}_default', 's_U95_p10': f'{pre}_s_U95_p10',
             'jev1': 'base_jev1' if c == 'base' else f'{pre}_jev', 'jev2': f'{pre}_jevrr'}
    U0, it0 = read_U(runs / names['default']); n0 = np.linalg.norm(U0); um = np.linalg.norm(U0, axis=1).mean()
    for m in ('s_U95_p10', 'jev1', 'jev2'):
        U, it = read_U(runs / names[m]); d = np.linalg.norm(U - U0, axis=1)
        out.write(f"{c},{m},{it},{np.linalg.norm(d) / n0:.6e},{np.percentile(d, 99) / um:.6e},{d.max() / um:.6e}\n"); out.flush()
        print(c, m, it, np.linalg.norm(d) / n0, flush=True)
