"""Iterations to convergence of every method on all 22 conditions (final controller).
usage: fig_iters.py <data_dir> <out_prefix>"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import DESIGN, HELD1, HELD2, LABEL, family, load, sort_key
from style import C_DEF, C_HEU, C_JEV, C_ORA, INK2, setup

setup()
data = Path(sys.argv[1]); out = sys.argv[2]
d = load(data); s = pd.read_csv(data / "derived" / "summary_v4.csv").set_index("case")
cases = sorted(s.index, key=sort_key)
CAP = {"cavity": 20000, "lbfs": 20000, "pitzDaily": 5000, "pm_bfs": 4000}
fig, ax = plt.subplots(figsize=(7.2, 5.4))
y, yt, prev = 0, [], None
for c in cases:
    if prev and family(c) != prev:
        y += 0.8
    prev = family(c); r = s.loc[c]; cap = CAP[family(c)]
    ax.plot([1, 1e5], [y, y], color="#efeeea", lw=0.6, zorder=0)
    ax.plot(r.default_it if r.default_ok else cap, y, marker="o" if r.default_ok else "x", color=C_DEF, ms=6 if r.default_ok else 6, mew=1.6, ls="none", zorder=2)
    ax.plot(r.oracle_it, y, marker="s", color=C_ORA, ms=5, ls="none", zorder=3)
    ax.plot(r.heur_it if r.heur_conv else cap, y + 0.0, marker="D" if r.heur_conv else "x", color=C_HEU, ms=4.6 if r.heur_conv else 6, mew=1.6, ls="none", zorder=4)
    for kind, fill, dy in (("jev4", True, 0.16), ("jev4_async", False, -0.16)):
        q = d[(d.template == c) & d.group.isin(["v4", "v4held2"]) & (d.kind == kind)]
        for _, x in q.iterrows():
            if x.ok:
                ax.plot(x.iterations, y + dy, marker="o", ms=4.2, mfc=C_JEV if fill else "white", mec=C_JEV, mew=1.1, ls="none", zorder=5)
            else:
                ax.plot(cap, y + dy, marker="x", color=C_JEV, ms=5, mew=1.3, ls="none", zorder=5)
    yt.append((y, LABEL[c] + ("" if c in DESIGN else (r" $\cdot$ H1" if c in HELD1 else r" $\cdot$ H2"))))
    y += 1
ax.set_yticks([a for a, _ in yt]); ax.set_yticklabels([b for _, b in yt], fontsize=7.5); ax.invert_yaxis()
ax.set_xscale("log"); ax.set_xlim(150, 26000); ax.set_xlabel("outer iterations to convergence (log scale);  × at the iteration cap: not converged")
ax.grid(axis="y", visible=False)
h = [plt.Line2D([], [], marker="o", color=C_DEF, ls="none", ms=6, label="default factors"),
     plt.Line2D([], [], marker="s", color=C_ORA, ls="none", ms=5, label="best fixed pair (grid search)"),
     plt.Line2D([], [], marker="D", color=C_HEU, ls="none", ms=4.6, label="rule-based controller"),
     plt.Line2D([], [], marker="o", mfc=C_JEV, mec=C_JEV, ls="none", ms=4.2, label="judgment model, synchronous (3 repeats)"),
     plt.Line2D([], [], marker="o", mfc="white", mec=C_JEV, ls="none", ms=4.2, label="judgment model, asynchronous (3 repeats)")]
ax.legend(handles=h, ncol=3, fontsize=7.3, loc="upper center", bbox_to_anchor=(0.42, 1.09), columnspacing=1.2, handletextpad=0.3)
fig.savefig(out + ".pdf"); fig.savefig(out + ".png", dpi=170)
