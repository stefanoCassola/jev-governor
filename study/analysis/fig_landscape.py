"""Fixed-factor landscape: iterations to convergence on the (alpha_U, alpha_p) grid, with the factor path of one
run of the final controller. usage: fig_landscape.py <data_dir> <out_prefix>"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm

from common import load
from style import C_HEU, INK, INK2, SEQ_BLUE, setup

setup()
d = load(Path(sys.argv[1])); out = sys.argv[2]
PANELS = [("cavity_Re1000", "main", "v4", "(a) Cavity $Re=1000$ (SIMPLE)"), ("lbfs_Re400", "main", "v4", "(b) Laminar step $Re=400$ (SIMPLE)"),
          ("pitzDaily", "main", "v4", "(c) pitzDaily 10 m/s (SIMPLEC)"), ("pm_bfs_U44", "main", "v4", "(d) P--M step 44.2 m/s (SIMPLEC)")]
cmap = LinearSegmentedColormap.from_list("seqblue", SEQ_BLUE)
fig, axes = plt.subplots(2, 2, figsize=(7.0, 4.7), gridspec_kw={"wspace": 0.22, "hspace": 0.5}); axes = axes.ravel()
for ax, (case, gg, cg, title) in zip(axes, PANELS):
    g = d[(d.template == case) & (d.group == gg) & d.kind.str.startswith("grid")].copy()
    g["aU"] = g.kind.str.extract(r"U([\d.]+)_")[0].astype(float); g["ap"] = g.kind.str.extract(r"_p([\d.]+)")[0].astype(float)
    us, ps = sorted(g.aU.unique()), sorted(g.ap.unique())
    Z = np.full((len(ps), len(us)), np.nan)
    for _, r in g.iterrows():
        if r.ok:
            Z[ps.index(r.ap), us.index(r.aU)] = r.iterations
    norm = LogNorm(vmin=np.nanmin(Z), vmax=np.nanmax(Z))
    ax.imshow(np.zeros_like(Z), cmap="Greys", vmin=0, vmax=8, origin="lower", aspect="auto")
    ax.imshow(Z, cmap=cmap, norm=norm, origin="lower", aspect="auto")
    for i in range(len(ps)):
        for j in range(len(us)):
            v = Z[i, j]
            if np.isnan(v):
                ax.text(j, i, "×", ha="center", va="center", fontsize=8, color=INK2)
            else:
                ax.text(j, i, f"{v:.0f}" if v < 10000 else f"{v / 1000:.0f}k", ha="center", va="center", fontsize=6.5,
                        color="white" if norm(v) > 0.55 else INK)
    bi = np.unravel_index(np.nanargmin(Z), Z.shape)
    ax.add_patch(plt.Rectangle((bi[1] - 0.5, bi[0] - 0.5), 1, 1, fill=False, ec=C_HEU, lw=1.6))
    ax.set_xticks(range(len(us))); ax.set_xticklabels([f"{u:g}" for u in us], fontsize=7)
    ax.set_yticks(range(len(ps))); ax.set_yticklabels([f"{p:g}" for p in ps], fontsize=7)
    ax.set_xlabel(r"$\alpha_U$", labelpad=1); ax.set_title(title, loc="left", fontsize=8.5); ax.grid(False)
    ax.tick_params(length=2)
[a.set_ylabel(r"$\alpha_p$", labelpad=1) for a in axes[::2]]
fig.savefig(out + ".pdf"); fig.savefig(out + ".png", dpi=200)
