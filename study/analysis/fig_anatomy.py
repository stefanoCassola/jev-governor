"""Anatomy of selected benchmark runs: distance to the convergence criterion and the momentum-factor path.

usage: fig_anatomy.py <bench_selected.json> <out_prefix>
Distance = max over equations of log10(residual / target); the run stops when it reaches zero.
"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from style import C_DEF, C_HEU, C_JEV, C_ORA, C_RED, INK2, setup

setup()
h = json.load(open(sys.argv[1])); out = sys.argv[2]
COLS = [
    ("(a) P--M step, 25 m/s", 1500, [("main/pm_bfs_U25__default", "template 0.5/0.5 (n.c.)", C_DEF),
                                      ("main/pm_bfs_U25__grid_U0.98_p0.9", "best fixed 0.98/0.9", C_ORA),
                                      ("v3/pm_bfs_U25__jev3_r0", "model, variant P (n.c.)", C_RED),
                                      ("v4/pm_bfs_U25__jev4_r0", "model, variant E", C_JEV)]),
    ("(b) Laminar step, $Re=400$", 4200, [("main/lbfs_Re400__default", "default 0.7/0.3", C_DEF),
                                           ("main/lbfs_Re400__grid_U0.8_p0.3", "best fixed 0.8/0.3", C_ORA),
                                           ("v3/lbfs_Re400__jev3_r0", "model, variant P", C_RED),
                                           ("v4/lbfs_Re400__jev4_r0", "model, variant E (n.c.)", C_JEV)]),
    ("(c) Cavity, $Re=1000$", 3100, [("main/cavity_Re1000__default", "default 0.7/0.3", C_DEF),
                                      ("main/cavity_Re1000__grid_U0.9_p0.1", "best fixed 0.9/0.1", C_ORA),
                                      ("v4/cavity_Re1000__heur4", "rules, variant E", C_HEU),
                                      ("v4/cavity_Re1000__jev4_r0", "model, variant E", C_JEV)]),
]


def distance(v):
    arr = []
    for f, t in v["targets"].items():
        r = np.array([x if x else np.nan for x in v["res"].get(f, [])], float)
        arr.append(np.log10(r / t))
    n = min(map(len, arr))
    return np.array(v["it"][:n]), np.nanmax(np.vstack([a[:n] for a in arr]), axis=0)


def smooth(y, w=9):
    if len(y) < w:
        return y
    k = np.ones(w) / w
    return np.convolve(np.pad(y, (w // 2, w // 2), mode="edge"), k, mode="valid")


fig, axes = plt.subplots(2, 3, figsize=(7.4, 4.5), sharex="col", gridspec_kw={"height_ratios": [1.5, 1], "hspace": 0.1, "wspace": 0.24})
for j, (title, xmax, runs) in enumerate(COLS):
    a0, a1 = axes[0, j], axes[1, j]
    for key, lab, col in runs:
        v = h[key]; it, dist = distance(v)
        a0.plot(it, smooth(dist), color=col, lw=1.3, label=lab)
        if v["result"]["converged"]:
            a0.plot(it[-1], 0, "o", color=col, ms=4.5, mec="white", mew=0.8, zorder=5)
        ff = v["result"]["final_factors"]
        if v.get("decisions"):
            u0 = {"pm": 0.5, "lb": 0.7, "ca": 0.7}[key.split("/")[1][:2]]
            xs, us = [0], [u0]
            for dcs in v["decisions"]:
                xs += [dcs["it"], dcs["it"]]; us += [us[-1], dcs["U"]]
            xs.append(it[-1]); us.append(us[-1])
            a1.plot(xs, np.array(us) / (1 - np.array(us)), color=col, lw=1.2)
        else:
            init = v["result"].get("init")
            u = float(init.split()[0]) if isinstance(init, str) and init else (init[0] if init else ff["U"])
            a1.plot([0, it[-1]], [u / (1 - u)] * 2, color=col, lw=1.2)
    a0.axhline(0, color=INK2, lw=0.6, ls=":"); a0.set_xlim(0, xmax); a0.set_ylim(-0.3, 4.6)
    a0.set_title(title, loc="left"); a0.legend(fontsize=6.6, loc="upper right", handlelength=1.2, labelspacing=0.3)
    a1.set_yscale("log"); a1.set_yticks([1, 2.33, 4, 9, 19, 49, 99]); a1.set_yticklabels(["0.5", "0.7", "0.8", "0.9", "0.95", "0.98", "0.99"], fontsize=7)
    a1.minorticks_off(); a1.set_ylim(0.8, 130); a1.set_xlabel("outer iteration")
    a0.tick_params(labelsize=7); a1.tick_params(axis="x", labelsize=7)
axes[0, 0].set_ylabel("distance to the criterion (decades)"); axes[1, 0].set_ylabel(r"$\alpha_U$  (scale of $\tau$)")
fig.savefig(out + ".pdf"); fig.savefig(out + ".png", dpi=170)
