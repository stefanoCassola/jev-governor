"""Solution accuracy at the stopping point: relative L2 velocity error against long reference runs.

usage: make_accuracy.py <data_dir> <results_dir>
References: cavities and laminar step: default factors with all residual targets tightened by 1e3 (converged).
pitzDaily and the Pawar-Maulik step cannot reach such targets (their residuals level off), so the reference is
a 20,000 / 16,000 iteration run with the best fixed pair (results_uerr_ref2.csv); a second long run with
different factors measures how well the reference itself is defined. The first version of this analysis used
default-factor runs as reference for these two families; on the Pawar-Maulik step those had not converged,
which inflated all errors at 25 and 30 m/s to about 22 %.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import LABEL, family, load, sort_key, write_numbers

data, out = Path(sys.argv[1]), Path(sys.argv[2])
d = load(data)
e = pd.read_csv(data / "results_uerr_ref2.csv")
d = d.merge(e[["group", "run", "u_err_ref2"]], on=["group", "run"], how="left")
d["err"] = np.where(d.template.str.startswith(("pm_bfs", "pitzDaily")), d.u_err_ref2, d.u_err_l2)
M = {}


def macro(k, v, nd=2):
    assert k.isalpha(); M[k] = f"{v:.{nd}f}"


s4 = pd.read_csv(data / "derived" / "summary_v4.csv").set_index("case")
rows = []
for c in sorted(s4.index, key=sort_key):
    grp = ["v4held2"] if s4.loc[c].set == "H2" else ["v4"]
    q = d[(d.template == c) & d.group.isin(grp) & d.ok]
    gg = "main" if s4.loc[c].set == "D" else ("v3held" if s4.loc[c].set == "H1" else "v4held2")
    pair = s4.loc[c].oracle_pair.split("/")
    o = d[(d.template == c) & (d.group == gg) & (d.kind == f"grid_U{pair[0]}_p{pair[1]}")]
    rows.append({"case": c, "family": family(c),
                 "default": q[q.kind == "default"].err.mean(), "oracle": o.err.mean(),
                 "rules": q[q.kind == "heur4"].err.mean(), "model_sync": q[q.kind == "jev4"].err.mean(),
                 "model_async": q[q.kind == "jev4_async"].err.mean()})
t = pd.DataFrame(rows); t.to_csv(data / "derived" / "accuracy.csv", index=False)
for fam, F in (("cavity", "Cav"), ("pitzDaily", "Pitz"), ("pm_bfs", "Pm")):
    q = t[t.family == fam]
    for col, C in (("default", "Def"), ("oracle", "Ora"), ("rules", "Heur"), ("model_sync", "Jev"), ("model_async", "Jeva")):
        v = 100 * q[col].dropna()
        macro(f"Acc{F}{C}Lo", v.min()); macro(f"Acc{F}{C}Hi", v.max())
both = t.dropna(subset=["default", "model_sync"])
macro("AccWorstExcess", 100 * (both.model_sync - both.default).max())
macro("AccNBetter", int((both.model_sync < both.default).sum()), 0); macro("AccNBoth", len(both), 0)
r2 = e[e.group == "ref2"].dropna(subset=["u_err_ref2b"]); r2 = r2[r2.run.str.endswith("__ref2")]
macro("AccRefPmLo", 100 * r2[r2.template.str.startswith("pm")].u_err_ref2b.min()); macro("AccRefPmHi", 100 * r2[r2.template.str.startswith("pm")].u_err_ref2b.max())
macro("AccRefPitz", 100 * r2[r2.template.str.startswith("pitz")].u_err_ref2b.max())
old = e[(e.group == "ref") & e.template.str.startswith("pm")]
lo = old[old.template.isin(["pm_bfs_U25", "pm_bfs_U30"])].u_err_ref2; hi = old[~old.template.isin(["pm_bfs_U25", "pm_bfs_U30"])].u_err_ref2
macro("AccOldRefLowLo", 100 * lo.min(), 0); macro("AccOldRefLowHi", 100 * lo.max(), 0)
macro("AccOldRefLo", 100 * hi.min(), 1); macro("AccOldRefHi", 100 * hi.max(), 1)

fd = pd.read_csv(data / "perm_fielddiff.csv")
for m, K in (("s_U95_p10", "Fix"), ("jev1", "One"), ("jev2", "Two")):
    v = 100 * fd[fd.method == m].rel_l2
    macro(f"Field{K}Lo", v.min()); macro(f"Field{K}Hi", v.max())
f1 = fd[(fd.method == "jev1") & (fd.case != "p90_x")]
macro("FieldOneOkHi", 100 * f1.rel_l2.max())
bad = fd[(fd.method == "jev1") & (fd.case == "p90_x")].iloc[0]
macro("FieldFalse", 100 * bad.rel_l2, 0); macro("FieldFalseMax", bad.max_over_mean, 0)
write_numbers(out / "numbers_acc.csv", M, "make_accuracy.py")
print(t.round(4).to_string()); print(open(out / "numbers_acc.csv").read())
