"""Anatomy of the false stop on the 90th-percentile permeability case.

usage: fig_perm_anatomy.py <histories.json> <fields_dir> <out_prefix>
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

from style import C_DEF, C_JEV, C_ORA, C_RED, INK2, setup

setup()
h = json.load(open(sys.argv[1])); fields = Path(sys.argv[2]); out = sys.argv[3]
RUNS = [("fg_p90_x_default", "default 0.9/0.6", C_DEF, "-"), ("fg_p90_x_s_U95_p10", "fixed 0.95/1.0", C_ORA, "-"),
        ("fg_p90_x_jev", "model, round 1 (stop switch ignored by the solver)", C_RED, "-"),
        ("fg_p90_x_jevrr", "model, round 2 (patched solver)", C_JEV, "-")]
Kref = h["fg_p90_x_default"]["K"][-1]

fig = plt.figure(figsize=(7.2, 4.3))
gs = fig.add_gridspec(2, 2, width_ratios=[1.45, 1], height_ratios=[1.5, 1], hspace=0.2, wspace=0.3)
a0 = fig.add_subplot(gs[0, 0]); a1 = fig.add_subplot(gs[1, 0], sharex=a0); a2 = fig.add_subplot(gs[:, 1])
for r, lab, col, ls in RUNS:
    v = h[r]; it = np.array(v["it"]); K = np.array(v["K"], float) / Kref
    a0.plot(it, K, color=col, lw=1.6, label=lab)
    a0.plot(it[-1], K[-1], "o", color=col, ms=5, mec="white", mew=1)
    if "events" in v:
        its, au = [0], [0.9]
        for e in v["events"]:
            if e.get("to") and e.get("from"):
                its += [e["iteration"], e["iteration"]]; au += [e["from"][0], e["to"][0]]
        its.append(it[-1]); au.append(au[-1])
        a1.plot(its, np.array(au) / (1 - np.array(au)), color=col, lw=1.6)
        for e in v["events"]:
            if e.get("kind") == "guard":
                a1.annotate("guard", (e["iteration"], e["to"][0] / (1 - e["to"][0])), xytext=(8, -2), textcoords="offset points",
                            fontsize=7, color=INK2)
a1.axhline(9, color=C_DEF, lw=1.2); a1.axhline(19, color=C_ORA, lw=1.2)
a0.axhline(1, color=INK2, lw=0.5, ls=":")
a0.set_ylim(0.8, 1.04); a0.set_xlim(0, 600); a0.set_ylabel("$K/K_\\mathrm{default}$")
a0.annotate("false stop at iteration 90\n$\\Delta K=-2.4$ %", (90, h["fg_p90_x_jev"]["K"][-1] / Kref), xytext=(300, 0.905),
            fontsize=7.5, color=INK2, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.5))
a0.legend(fontsize=6.8, loc="lower right", handlelength=1.4, borderaxespad=0.2); plt.setp(a0.get_xticklabels(), visible=False)
a0.set_title("(a) permeability during the run", loc="left")
a1.set_yscale("log"); a1.set_yticks([9, 19, 49, 99]); a1.set_yticklabels(["9", "19", "49", "99"]); a1.minorticks_off()
a1.set_ylabel(r"$\tau=\alpha_U/(1-\alpha_U)$"); a1.set_xlabel("outer iteration")
a1.set_title("(b) pseudo-time factor", loc="left")

za, zb = np.load(fields / "fg_p90_x_jev.npz"), np.load(fields / "fg_p90_x_default.npz")
n = za["fluid"].shape[2]; k = n // 2
ux_a, ux_b = za["U"][:, :, k, 0], zb["U"][:, :, k, 0]
um = float(zb["U"][..., 0][zb["fluid"]].mean())
diff = np.where(zb["fluid"][:, :, k], (ux_a - ux_b) / um, np.nan)
L = n * float(zb["h"]) * 1e6
cm = LinearSegmentedColormap.from_list("div", ["#184f95", "#3987e5", "#f0efec", "#e66767", "#a82525"]); cm.set_bad("#dcdad2")
lim = float(np.nanpercentile(np.abs(diff), 99.5))
im = a2.imshow(diff.T, origin="lower", extent=(0, L, 0, L), cmap=cm, norm=TwoSlopeNorm(0, -lim, lim), interpolation="nearest")
a2.set_xlabel("$x$ (µm), flow direction"); a2.set_ylabel("$y$ (µm)"); a2.grid(False)
a2.set_title("(c) what the false stop leaves unconverged", loc="left")
cb = fig.colorbar(im, ax=a2, orientation="horizontal", fraction=0.046, pad=0.14); cb.outline.set_visible(False)
cb.set_label(r"$(u_x^{\,\mathrm{false\ stop}}-u_x^{\,\mathrm{converged}})/\langle u_x\rangle$ in the plane $z=80$ µm", fontsize=7.5)
cb.ax.tick_params(labelsize=7)
fig.savefig(out + ".pdf", dpi=250); fig.savefig(out + ".png", dpi=170)
g = zb["fluid"]
print("global: mean ux false/conv", float(za["U"][..., 0][g].mean() / zb["U"][..., 0][g].mean()),
      "rel L2 diff", float(np.linalg.norm((za["U"] - zb["U"])[g]) / np.linalg.norm(zb["U"][g])), "lim", lim)
