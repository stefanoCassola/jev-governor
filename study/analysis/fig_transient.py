"""Transient study: outer iterations per time step and factor paths of selected runs.
usage: fig_transient.py <transient_selected.json> <out_prefix>"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from style import C_DEF, C_HEU, C_JEV, C_ORA, INK2, setup

setup()
h = json.load(open(sys.argv[1])); out = sys.argv[2]
cases = sorted({k.split("/")[1].split("__")[0] for k in h}, reverse=True)
TITLE = {"pitzT_U20_dt1": "pitzDaily, 20 m/s, $\\Delta t$ = 0.1 ms (held out)",
         "cav3T_Re400_dt30": "3D cavity, $Re$ = 400, $\\Delta t$ = 0.3 (held out): crash"}
fig, axes = plt.subplots(2, len(cases), figsize=(7.4, 3.6), sharex="col",
                         gridspec_kw={"height_ratios": [1.6, 1], "hspace": 0.1, "wspace": 0.22})
axes = np.atleast_2d(axes).reshape(2, len(cases))


def smooth(y, w=5):
    y = np.asarray(y, float)
    return np.convolve(np.pad(y, (w // 2, w // 2), mode="edge"), np.ones(w) / w, mode="valid")


for j, c in enumerate(cases):
    a0, a1 = axes[0, j], axes[1, j]
    for key, v in h.items():
        if f"/{c}__" not in key:
            continue
        kind = key.split("__")[1]
        col, lab = ((C_DEF, "default 0.7/0.3") if kind == "default" else (C_ORA, "best fixed " + kind.replace("grid_U", "").replace("_p", "/"))
                    if kind.startswith("grid") else (C_HEU, "rules") if kind.startswith("heurT") else (C_JEV, "model"))
        n = np.array(v["n_outer"]); x = np.arange(1, len(n) + 1)
        a0.plot(x, smooth(n), color=col, lw=1.3, label=lab, zorder=3 if col == C_JEV else 2)
        bad = np.where(np.array(v["converged"]) == 0)[0]
        if kind.startswith(("jevT", "heurT")) and len(bad):
            a0.plot(x[bad], np.full(len(bad), 49), "|", color=col, ms=5, mew=1)
        if v.get("decisions"):
            xs, us, ps = [0], [0.7], [0.3]
            for dcs in v["decisions"]:
                xs += [dcs["step"], dcs["step"]]; us += [us[-1], dcs["U"]]; ps += [ps[-1], dcs["p"]]
            xs.append(len(n)); us.append(us[-1]); ps.append(ps[-1])
            if kind.startswith("jevT"):
                a1.plot(xs, us, color=col, lw=1.3); a1.plot(xs, ps, color=col, lw=1.3, ls="--")
    a0.set_ylim(0, 52); a0.set_title(f"({'abcd'[j]}) " + TITLE.get(c, c), loc="left"); a0.tick_params(labelsize=7)
    a1.set_ylim(0.2, 1.0); a1.set_xlabel("time step"); a1.tick_params(labelsize=7)
    if True:
        a0.legend(fontsize=6.6, loc="upper right", handlelength=1.2, labelspacing=0.3)
axes[0, 0].set_ylabel("outer iterations per step"); axes[1, 0].set_ylabel(r"$\alpha_U$ (—), $\alpha_p$ (- -)")
fig.savefig(out + ".pdf"); fig.savefig(out + ".png", dpi=170)
