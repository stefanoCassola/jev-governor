"""Permeability study: table, bar figure and named numbers.

usage: make_perm.py <data_dir> <results_dir>
Times are run_s: from the end of the first solver iteration to the end of the run (mesh reading, which took
2.5-65 min depending on how many 17-million-cell meshes were read at once, is excluded for every method).
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import gmean, write_numbers, write_table
from style import C_DEF, C_JEV, C_ORA, INK2, setup

data, out = Path(sys.argv[1]), Path(sys.argv[2])
d = pd.read_csv(data / "perm_results.csv")
CASES = [("base", "Base ($x$)", 17.5), ("med_x", "Median $x$", 17.5), ("med_y", "Median $y$", 14.9),
         ("med_z", "Median $z$", 17.7), ("p90_x", "90th pct.\\ $x$", 16.3), ("fvc56_x", "$V_f$ 56\\,\\% $x$", 14.2),
         ("long_y", "Long $y$", 17.7)]
M = {}


def macro(k, v, nd=0):
    assert k.isalpha()
    M[k] = v if isinstance(v, str) else f"{v:,.{nd}f}".replace(",", "{,}")


def pct(x):
    return ("+" if x >= 0 else "$-$") + f"{abs(x):.2f}"


rows, lines = [], [r"\begin{tabular}{@{}lrrrrrrrrr@{}}", r"\toprule",
                   r" & Cells & \multicolumn{2}{c}{Default 0.9/0.6} & \multicolumn{2}{c}{Fixed 0.95/1.0} & "
                   r"\multicolumn{2}{c}{Model, round 1} & \multicolumn{2}{c}{Model, round 2} \\",
                   r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(lr){7-8}\cmidrule(lr){9-10}",
                   r"Case & $10^6$ & it. & h & it.\ ($t$) & $\Delta K$ \% & it.\ ($t$) & $\Delta K$ \% & it.\ ($t$) & $\Delta K$ \% \\",
                   r"\midrule"]
for c, lab, cells in CASES:
    q = d[(d.case == c) & d.method.isin(["default", "s_U95_p10", "jev1", "jev2"])].set_index("method")
    assert q.converged.all() and not q.fatal.any()
    df = q.loc["default"]
    r = {"case": c, "cells_M": cells, "default_it": df.iterations, "default_h": df.run_s / 3600, "default_K": df.perm}
    cellsx = [lab, f"{cells}", f"{int(df.iterations):,}".replace(",", "{,}"), f"{df.run_s / 3600:.1f}"]
    for m in ("s_U95_p10", "jev1", "jev2"):
        x = q.loc[m]
        r[f"{m}_it"], r[f"{m}_itr"], r[f"{m}_tr"] = x.iterations, x.iterations / df.iterations, x.run_s / df.run_s
        r[f"{m}_dK"] = 100 * (x.perm / df.perm - 1); r[f"{m}_h"] = x.run_s / 3600
        flag = r"$^\dagger$" if abs(r[f"{m}_dK"]) > 1 else ""
        cellsx += [f"{int(x.iterations)} ({r[f'{m}_tr']:.2f})", pct(r[f"{m}_dK"]) + flag]
    rows.append(r); lines.append(" & ".join(cellsx) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]

write_table(out / "tables" / "tab_perm.md", lines)
s = pd.DataFrame(rows); (data / "derived").mkdir(exist_ok=True); s.to_csv(data / "derived" / "perm_summary.csv", index=False)

for m, K in (("s_U95_p10", "Fix"), ("jev1", "One"), ("jev2", "Two")):
    macro(f"Perm{K}ItLo", s[f"{m}_itr"].min(), 2); macro(f"Perm{K}ItHi", s[f"{m}_itr"].max(), 2)
    macro(f"Perm{K}ItGm", gmean(s[f"{m}_itr"]), 2); macro(f"Perm{K}TGm", gmean(s[f"{m}_tr"]), 2)
    macro(f"Perm{K}TLo", s[f"{m}_tr"].min(), 2); macro(f"Perm{K}THi", s[f"{m}_tr"].max(), 2)
    macro(f"Perm{K}Hours", s[f"{m}_h"].sum(), 1)
    macro(f"Perm{K}DKMax", s[f"{m}_dK"].abs().max(), 2)
macro("PermDefHours", s.default_h.sum(), 1)
ok1 = s[s.jev1_dK.abs() < 1]
macro("PermOneDKMaxOk", ok1.jev1_dK.abs().max(), 2)
p90 = s.set_index("case").loc["p90_x"]
macro("PermFalseIt", p90.jev1_it); macro("PermFalseDK", abs(p90.jev1_dK), 1)
macro("PermFixedIt", p90.jev2_it); macro("PermFixedDK", abs(p90.jev2_dK), 2); macro("PermFixedRatio", p90.jev2_itr, 2)
macro("PermNFewerThanFixed", int((s.jev2_it < s.s_U95_p10_it).sum()))
macro("PermMaxGainVsFixed", (s.s_U95_p10_it / s.jev2_it).max(), 1)
macro("PermCellsLo", s.cells_M.min(), 1); macro("PermCellsHi", s.cells_M.max(), 1)
# ------------------------------------------------------------------ control: fixed pairs closer to one (audit request)
# status of a run: ok (stopped by the rule, |dK| <= 1 %), false stop (stopped by the rule, |dK| > 1 %),
# diverged (cancelled by hand once the permeability was negative or orders of magnitude off), running.
EXTRA = [m for m in ("s_U98_p10", "s_U99_p10") if (d.method == m).any()]
if EXTRA:
    if "diverged" not in d:
        d["diverged"] = False
    lines = [r"\begin{tabular}{@{}lrrrrrrrr@{}}", r"\toprule",
             r" & \multicolumn{2}{c}{Fixed 0.95/1.0} & \multicolumn{2}{c}{Fixed 0.98/1.0} & \multicolumn{2}{c}{Fixed 0.99/1.0} & "
             r"\multicolumn{2}{c}{Model, round 2} \\",
             r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}",
             r"Case & it.\ ($t$) & $\Delta K$ \% & it.\ ($t$) & $\Delta K$ \% & it.\ ($t$) & $\Delta K$ \% & it.\ ($t$) & $\Delta K$ \% \\", r"\midrule"]
    xr = []
    for c, lab, cells in CASES:
        q = d[d.case == c].set_index("method"); df = q.loc["default"]; cellsx = [lab]
        for m in ("s_U95_p10", "s_U98_p10", "s_U99_p10", "jev2"):
            r = {"case": c, "method": m}
            if m not in q.index:
                r["status"] = "not run"; cellsx += ["--", "--"]
            elif bool(q.loc[m].diverged):
                r["status"] = "diverged"; r["it"] = q.loc[m].iterations
                cellsx += [r"\multicolumn{2}{c}{diverged}"]
            elif not bool(q.loc[m].finished):
                r["status"] = "running"; cellsx += [r"\multicolumn{2}{c}{running}"]
            else:
                x = q.loc[m]; dK = 100 * (x.perm / df.perm - 1)
                r.update(status="ok" if abs(dK) <= 1 else "false stop", it=x.iterations, itr=x.iterations / df.iterations,
                         tr=x.run_s / df.run_s, h=x.run_s / 3600, dK=dK)
                cellsx += [f"{int(x.iterations)} ({r['tr']:.2f})", pct(dK) + (r"$^\dagger$" if abs(dK) > 1 else "")]
            xr.append(r)
        lines.append(" & ".join(cellsx) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    write_table(out / "tables" / "tab_perm_fixed.md", lines)
    x = pd.DataFrame(xr); x.to_csv(data / "derived" / "perm_fixed_scan.csv", index=False)
    j2 = x[x.method == "jev2"].set_index("case")
    for m, K in (("s_U98_p10", "NineEight"), ("s_U99_p10", "NineNine")):
        q = x[x.method == m]
        for st, S in (("ok", "NOk"), ("false stop", "NFalse"), ("diverged", "NDiv"), ("running", "NRunning")):
            macro(f"Perm{K}{S}", int((q.status == st).sum()))
        fs = q[q.status == "false stop"]
        if len(fs):
            macro(f"Perm{K}FalseDKLo", fs.dK.abs().min(), 1); macro(f"Perm{K}FalseDKHi", fs.dK.abs().max(), 1)
        fin = q[q.status.isin(["ok", "false stop"])]
        if len(fin) == len(CASES):
            macro(f"Perm{K}HoursAll", fin.h.sum(), 1)
        ok = q[q.status == "ok"].set_index("case")
        if len(ok):
            macro(f"Perm{K}ItLo", ok.itr.min(), 2); macro(f"Perm{K}ItHi", ok.itr.max(), 2)
            macro(f"Perm{K}DKMax", ok.dK.abs().max(), 2)
            ratio = (j2.loc[ok.index, "it"] / ok.it)
            macro(f"Perm{K}ModelVsLo", ratio.min(), 2); macro(f"Perm{K}ModelVsHi", ratio.max(), 2)
            macro(f"Perm{K}ModelVsGm", gmean(ratio), 2)

# ------------------------------------------------------------------ one compact table of all permeability runs
if EXTRA:
    xs = x.set_index(["case", "method"])
    lines = [r"\begin{tabular}{@{}lrrrrrrrr@{}}", r"\toprule",
             r" & Cells & \multicolumn{2}{c}{Default 0.9/0.6} & \multicolumn{3}{c}{Fixed pair, it.\ ($t$)} & \multicolumn{2}{c}{Model, it.\ ($t$)} \\",
             r"\cmidrule(lr){3-4}\cmidrule(lr){5-7}\cmidrule(lr){8-9}",
             r"Case & $10^6$ & it. & h & 0.95/1.0 & 0.98/1.0 & 0.99/1.0 & round 1 & round 2 \\", r"\midrule"]
    worst_ok = 0.0
    for c, lab, cells in CASES:
        r0 = s.set_index("case").loc[c]
        row = [lab, f"{cells}", f"{int(r0.default_it):,}".replace(",", "{,}"), f"{r0.default_h:.1f}"]
        for m in ("s_U95_p10", "s_U98_p10", "s_U99_p10", "jev1", "jev2"):
            if m == "jev1":
                it, tr, dK = r0.jev1_it, r0.jev1_tr, r0.jev1_dK
                st = "ok" if abs(dK) <= 1 else "false stop"
            else:
                q = xs.loc[(c, m)]; st = q.status
                it, tr, dK = q.get("it"), q.get("tr"), q.get("dK")
            if st == "diverged":
                row.append("div.")
            else:
                row.append(f"{int(it)} ({tr:.2f})" + (r"$^\dagger$" if st == "false stop" else ""))
                if st == "ok":
                    worst_ok = max(worst_ok, abs(dK))
        lines.append(" & ".join(row) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    write_table(out / "tables" / "tab_perm_all.md", lines)
    macro("PermWorstOkDK", worst_ok, 2)

write_numbers(out / "numbers_perm.csv", M, "make_perm.py")

setup()
fig, ax = plt.subplots(figsize=(6.4, 2.6))
x = np.arange(len(s)); w = 0.26
for i, (m, lab, col, hatch) in enumerate((("s_U95_p10", "fixed 0.95/1.0", C_ORA, None), ("jev1", "model, round 1 (stock solver)", C_JEV, "////"),
                                         ("jev2", "model, round 2 (patched solver)", C_JEV, None))):
    b = ax.bar(x + (i - 1) * w, s[f"{m}_itr"], w * 0.9, color=col if hatch is None else "white", edgecolor=col, hatch=hatch,
               linewidth=0.8, label=lab)
    for xi, (v, dk) in enumerate(zip(s[f"{m}_itr"], s[f"{m}_dK"])):
        if abs(dk) > 1:
            ax.annotate(f"false stop\n$\\Delta K$ = {dk:.1f} %", (xi + (i - 1) * w, v), xytext=(0, 14), textcoords="offset points",
                        ha="center", fontsize=7, color=INK2, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.5))
ax.axhline(1, color=C_DEF, lw=1); ax.text(len(s) - 0.45, 1.02, "default 0.9/0.6", ha="right", fontsize=7.5, color=INK2)
ax.set_xticks(x); ax.set_xticklabels([r.replace("\\,", " ").replace("\\ ", " ").replace("\\%", "%").replace("$V_f$", "$V_f$") for _, r, _ in CASES], fontsize=7.5)
ax.set_ylabel("iterations relative to default"); ax.set_ylim(0, 1.12); ax.grid(axis="x", visible=False)
ax.legend(ncol=3, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, 1.22))
(out / "figures").mkdir(parents=True, exist_ok=True); fig.savefig(out / "figures" / "fig_perm_bars.pdf"); fig.savefig(out / "figures" / "fig_perm_bars.png")
print(open(out / "numbers_perm.csv").read()); print(open(out / "tables" / "tab_perm.md").read())
