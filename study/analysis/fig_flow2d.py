"""Flow fields of the four benchmark families (velocity magnitude and streamlines).

usage: fig_flow2d.py <vtk_root> <out_dir>
<vtk_root> holds foamToVTK exports of the final fields of one converged run per family.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyvista as pv
from matplotlib.colors import LinearSegmentedColormap

from style import INK2, SEQ_BLUE, setup

setup()
root, out = Path(sys.argv[1]), Path(sys.argv[2])
CASES = [  # dir, label, x-range shown, y-range, reference velocity, grid (nx, ny), streamline density
    ("v4_cavity_Re1000__jev4_r0", "(a) Lid-driven cavity, $Re=1000$", None, None, (220, 220), 1.2),
    ("v3_lbfs_Re400__jev3_r0", "(b) Laminar step, $Re=400$", (0, 12), None, (900, 140), (1.6, 0.7)),
    ("v4_pitzDaily__jev4_r0", "(c) pitzDaily, 10 m/s, $k$--$\\epsilon$", None, None, (900, 160), (1.6, 0.7)),
    ("v4_pm_bfs_U44__jev4_r0", "(d) Pawar--Maulik step, 44.2 m/s, $k$--$\\omega$ SST (near-step region)",
     (-0.05, 0.22), (0, 0.05), (900, 200), (1.6, 0.7)),
]
cmap = LinearSegmentedColormap.from_list("seqblue", ["#ffffff"] + SEQ_BLUE)


def load(d: Path):
    vtu = next(d.glob("VTK/*/internal.vtu"))
    m = pv.read(vtu)
    return m.cell_data_to_point_data()


fig = plt.figure(figsize=(7.2, 3.9))
gs = fig.add_gridspec(3, 2, width_ratios=[1, 1.75], hspace=0.95, wspace=0.28)
axes = [fig.add_subplot(gs[:, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 1]), fig.add_subplot(gs[2, 1])]
for ax, (d, label, xr, yr, (nx, ny), dens) in zip(axes, CASES):
    m = load(root / d)
    b = m.bounds
    x0, x1 = xr if xr else (b[0], b[1])
    y0, y1 = yr if yr else (b[2], b[3])
    zc = 0.5 * (b[4] + b[5])
    xs, ys = np.linspace(x0, x1, nx), np.linspace(y0, y1, ny)
    X, Y = np.meshgrid(xs, ys)
    pts = pv.PolyData(np.c_[X.ravel(), Y.ravel(), np.full(X.size, zc)])
    s = pts.sample(m)
    ok = s["vtkValidPointMask"].reshape(X.shape).astype(bool)
    U = s["U"].reshape(ny, nx, 3)
    u, v = np.where(ok, U[..., 0], np.nan), np.where(ok, U[..., 1], np.nan)
    mag = np.hypot(u, v)
    pc = ax.pcolormesh(X, Y, mag, cmap=cmap, shading="auto", rasterized=True, vmin=0)
    ax.streamplot(xs, ys, np.nan_to_num(u), np.nan_to_num(v), density=dens, color=INK2, linewidth=0.45,
                  arrowsize=0.5, broken_streamlines=True)
    ax.contourf(X, Y, (~ok).astype(float), levels=[0.5, 1.5], colors=["#d9d8d3"])
    ax.set_aspect("equal" if "cavity" in d else "auto"); ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_title(label, loc="left", fontsize=8.5)
    ax.grid(False)
    ax.set_xlabel("$x$ (m)" if ("pitz" in d or "pm_" in d) else ("$x/H$" if "lbfs" in d else "$x/L$"), labelpad=1)
    ax.set_ylabel("$y$ (m)" if ("pitz" in d or "pm_" in d) else ("$y/H$" if "lbfs" in d else "$y/L$"), labelpad=1)
    ax.tick_params(labelsize=7)
    cb = (fig.colorbar(pc, ax=ax, orientation="horizontal", fraction=0.04, pad=0.16) if "cavity" in d
          else fig.colorbar(pc, ax=ax, fraction=0.03, pad=0.015))
    cb.set_label("$|U|$" + (" (m/s)" if ("pitz" in d or "pm_" in d) else ""), fontsize=7.5)
    cb.ax.tick_params(labelsize=7); cb.outline.set_visible(False)
    print(d, m.n_cells, "cells, max|U| =", float(np.nanmax(mag)))
fig.savefig(out / "fig_flow2d.pdf", dpi=300); fig.savefig(out / "fig_flow2d.png", dpi=200)
