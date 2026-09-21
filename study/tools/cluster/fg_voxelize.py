"""Map the final fields of voxel-mesh permeability runs onto the regular voxel grid.

usage: fg_voxelize.py <C file> <out_dir> <run_dir> [<run_dir> ...]
Writes <out_dir>/<run name>.npz with U (nx,ny,nz,3) float32, p (nx,ny,nz) float32, fluid mask, voxel size h.
"""
import re, sys
from pathlib import Path
import numpy as np


def read_field(path, kind):
    s = Path(path).read_bytes()
    m = re.search(rb'internalField\s+nonuniform\s+List<' + kind.encode() + rb'>\s*(\d+)\s*\(', s)
    n = int(m.group(1)); start = m.end(); end = s.find(b'\n)\n', start)
    body = s[start:end]; del s
    if kind == 'vector':
        body = body.replace(b'(', b' ').replace(b')', b' ')
    a = np.array(body.split(), dtype=np.float64); del body
    a = a.reshape(n, 3) if kind == 'vector' else a
    assert len(a) == n
    return a


def final_dir(run):
    ts = [p for p in Path(run).iterdir() if p.is_dir() and re.fullmatch(r'\d+', p.name) and p.name != '0']
    return max(ts, key=lambda p: int(p.name))


C = read_field(sys.argv[1], 'vector')
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
xs = np.unique(np.round(C[:, 0], 12)); h = float(np.min(np.diff(xs)))
lo = C.min(axis=0)
idx = np.rint((C - lo) / h).astype(np.int32)
shape = tuple(int(v) for v in idx.max(axis=0) + 1)
print('cells', len(C), 'h', h, 'shape', shape, 'origin', lo, flush=True)
assert len(np.unique(np.ravel_multi_index(idx.T, shape))) == len(C), 'voxel mapping not unique'
del C
for run in sys.argv[3:]:
    d = final_dir(run)
    U = read_field(d / 'U', 'vector').astype(np.float32)
    p = read_field(d / 'p', 'scalar').astype(np.float32)
    Ug = np.zeros(shape + (3,), np.float32); pg = np.zeros(shape, np.float32); mask = np.zeros(shape, bool)
    Ug[idx[:, 0], idx[:, 1], idx[:, 2]] = U; pg[idx[:, 0], idx[:, 1], idx[:, 2]] = p
    mask[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    np.savez_compressed(out / f'{Path(run).name}.npz', U=Ug, p=pg, fluid=mask, h=h, iteration=int(d.name))
    print('wrote', Path(run).name, d.name, 'mean|U|', float(np.linalg.norm(U, axis=1).mean()), flush=True)
