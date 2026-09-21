"""Transient (PIMPLE) study: table, named numbers and derived CSV.

usage: make_transient.py <data_dir> <results_dir>
Input: data/results_t.csv (one row per run; groups t_grid/t_gridH = fixed pairs of the design / held-out set,
t_design/t_held = default, rule-based twin and model-based controller, t_ref = references).
Cost: total number of PIMPLE outer iterations over the fixed number of time steps, reported per time step.
A run (fixed pair or controller) counts as successful only if it completed and at most 2 % of its steps hit the
outer-iteration cap.
Time: noise-reduced, T = n_outer_total * median(CPU time per outer iteration of that method on that case) + waiting + t0.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import gmean, write_numbers, write_table

data, out = Path(sys.argv[1]), Path(sys.argv[2])
d = pd.read_csv(data / "results_t.csv")
d["kind"] = d["run"].str.split("__").str[1].str.replace(r"_r\d+$", "", regex=True)
d["per_step"] = d.outer_total / d.steps
d["clean"] = d.completed.astype(bool) & (d.unconverged_steps <= 0.02 * d.steps_planned)
d["spo"] = d.exec_s / d.outer_total
d["other_s"] = d.wall_s - d.exec_s - d.controller_wait_s.fillna(0)
T0 = float(d[d.group.isin(["t_design", "t_held"])].other_s.median())
D = ["cavT_Re1000_dt05", "cavT_Re1000_dt10", "cav3T_Re1000_dt15", "cav3T_Re1000_dt30", "cylT_Re100_dt05",
     "cylT_Re100_dt10", "pitzT_U10_dt1", "pitzT_U10_dt2"]
H = ["cavT_Re400_dt10", "cavT_Re2500_dt05", "cav3T_Re400_dt30", "cav3T_Re2000_dt15", "cylT_Re150_dt05",
     "cylT_Re60_dt10", "pitzT_U20_dt1", "pitzT_U5_dt2"]
LAB = {"cavT": "Cavity 2D", "cav3T": "Cavity 3D", "cylT": "Cylinder", "pitzT": "pitzDaily"}
GOV = data / "results_t_governor.csv"          # jevGovernor 0.3.0 (PIMPLE mode) on the same 16 conditions
gv = pd.read_csv(GOV) if GOV.exists() else None
if gv is not None:
    gv["kind"] = gv["run"].str.split("__").str[1].str.replace(r"_r\d+$", "", regex=True)
    gv["per_step"] = gv.outer_total / gv.steps
    gv["clean"] = gv.completed.astype(bool) & (gv.unconverged_steps <= 0.02 * gv.steps_planned)
    gv["spo"] = gv.exec_s / gv.outer_total
M = {}


def macro(k, v, nd=2):
    assert k.isalpha(), k
    M[k] = v if isinstance(v, str) else f"{v:,.{nd}f}".replace(",", "{,}")


DT = {"cavT": {"dt05": "0.05", "dt10": "0.1"}, "cav3T": {"dt15": "0.15", "dt30": "0.3"},
      "cylT": {"dt05": "0.5~s", "dt10": "1~s"}, "pitzT": {"dt1": "0.1~ms", "dt2": "0.2~ms"}}


def label(c):
    fam, a, b = c.split("_")
    par = a.replace("Re", "$Re$ ").replace("U", "") + (" m/s" if fam == "pitzT" else "")
    return f"{LAB[fam]}, {par}"


rows = []
for c in D + H:
    hset = c in H
    g = d[(d.template == c) & (d.group == ("t_gridH" if hset else "t_grid"))]
    a = d[(d.template == c) & (d.group == ("t_held" if hset else "t_design"))]
    if not len(a):
        continue
    dflt = a[a.kind == "default"].iloc[0]
    r = {"case": c, "set": "H" if hset else "D", "family": c.split("_")[0], "steps": int(dflt.steps_planned),
         "co": float(dflt.co_max), "default": dflt.per_step, "default_clean": bool(dflt.clean),
         "default_T": dflt.outer_total * d[(d.template == c) & (d.kind == "default")].spo.median() + T0}
    gc = g[g.kind.str.startswith("grid") & g.clean]
    r["grid_n"], r["grid_clean"] = int(g.kind.str.startswith("grid").sum()), len(gc)
    r["grid_crashed"] = int((~g[g.kind.str.startswith("grid")].completed.astype(bool)).sum())
    if len(gc):
        b = gc.sort_values("outer_total").iloc[0]
        r["best"], r["best_pair"] = b.per_step, b.kind.replace("grid_U", "").replace("_p", "/")
        r["best_err"] = b.u_err_l2
    for tag, kind in (("heur", "heurT"), ("jev", "jevT"), ("jeva", "jevT_async")):
        q = a[a.kind == kind]; ok = q[q.clean]          # success: completed and at most 2 % of the steps capped
        r[f"{tag}_crashed"] = int((~q.completed.astype(bool)).sum())
        r[f"{tag}_n"], r[f"{tag}_ok"] = len(q), len(ok)
        if len(ok):
            r[tag] = ok.per_step.mean(); r[f"{tag}_sd"] = ok.per_step.std() if len(ok) > 1 else 0.0
            r[f"{tag}_unconv"] = ok.unconverged_steps.mean(); r[f"{tag}_err"] = ok.u_err_l2.mean()
            r[f"{tag}_wait"] = ok.controller_wait_s.mean(); r[f"{tag}_calls"] = ok.model_calls.mean()
            spo = d[(d.template == c) & (d.kind == kind)].spo.median()
            r[f"{tag}_T"] = ok.outer_total.mean() * spo + r[f"{tag}_wait"] + T0
            r[f"{tag}_finalU"], r[f"{tag}_finalp"] = ok.final_U.mean(), ok.final_p.mean()
    r["default_err"] = dflt.u_err_l2
    if gv is not None:
        q = gv[gv.template == c]
        gd = q[q.kind == "default"].iloc[0]
        r["gov_default_T"] = gd.outer_total * gd.spo + T0
        for tag, kind in (("govr", "govRules"), ("govj", "govJev"), ("govja", "govJev_async")):
            k = q[q.kind == kind]; done = k[k.completed.astype(bool)]
            r[f"{tag}_n"], r[f"{tag}_done"], r[f"{tag}_clean"] = len(k), len(done), int(k.clean.sum())
            if len(done):
                r[tag] = done.per_step.mean(); r[f"{tag}_slow"] = done.slow_capped_steps.mean()
                r[f"{tag}_unst"] = done.unstable_steps.mean(); r[f"{tag}_err"] = done.u_err_l2.mean()
                wait = done.api_latency_s.mean() if kind == "govJev" else 0.0
                r[f"{tag}_T"] = done.outer_total.mean() * k.spo.median() + wait + T0
    rows.append(r)
t = pd.DataFrame(rows)
(data / "derived").mkdir(exist_ok=True); t.to_csv(data / "derived" / "summary_transient.csv", index=False)

lines = [r"\begin{tabular}{@{}llrrrlrrrrrr@{}}", r"\toprule",
         r" & & & & \multicolumn{2}{c}{Best fixed pair} & \multicolumn{4}{c}{Research controller T} & \multicolumn{2}{c}{Governor 0.3.0} \\",
         r"\cmidrule(lr){5-6}\cmidrule(lr){7-10}\cmidrule(lr){11-12}",
         r"Condition, $\Delta t$ & Set & Co & Default & it. & $\aU/\ap$ & rules & model & async & capped & rules & model \\", r"\midrule"]
prev = None
for _, r in t.iterrows():
    if prev and r.family != prev:
        lines.append(r"\addlinespace")
    prev = r.family
    dt = DT[r.family][r.case.split("_")[2]]
    def cell(tag):
        if not r.get(f"{tag}_ok"):
            return "failed"
        s = f"{r[tag]:.1f}"
        return s if r[f"{tag}_ok"] == r[f"{tag}_n"] else s + f" ({int(r[f'{tag}_ok'])}/{int(r[f'{tag}_n'])})"
    lines.append(" & ".join([label(r.case) + f", {dt}", r.set, f"{r.co:.0f}", f"{r.default:.1f}" + ("" if r.default_clean else "$^*$"),
                             f"{r.best:.1f}" if pd.notna(r.get("best")) else "--", str(r.get("best_pair", "--")),
                             cell("heur"), cell("jev"), cell("jeva"),
                             f"{r.jev_unconv:.0f}/{r.steps}" if pd.notna(r.get("jev_unconv")) else "--",
                             (f"{r.govr:.1f}" + ("" if r.govr_clean == r.govr_n else "$^\\circ$")) if pd.notna(r.get("govr")) else "--",
                             (f"{r.govj:.1f}" + ("" if r.govj_clean == r.govj_n else "$^\\circ$")) if pd.notna(r.get("govj")) else "--"]) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(out / "tables" / "tab_transient.md", lines)

for S, q in (("All", t), ("Des", t[t.set == "D"]), ("Held", t[t.set == "H"])):
    if not len(q):
        continue
    for tag, K in (("best", "Best"), ("heur", "Heur"), ("jev", "Jev"), ("jeva", "Jeva")):
        if tag in q and q[tag].notna().any():
            rat = (q[tag] / q.default).dropna()
            macro(f"Tr{S}{K}Lo", rat.min()); macro(f"Tr{S}{K}Hi", rat.max()); macro(f"Tr{S}{K}Gm", gmean(rat))
    for tag, K in (("heur", "Heur"), ("jev", "Jev"), ("jeva", "Jeva")):
        if f"{tag}_T" in q:
            macro(f"Tr{S}{K}TGm", gmean((q[f"{tag}_T"] / q.default_T).dropna()))
            macro(f"Tr{S}{K}Ok", f"{int(q[f'{tag}_ok'].sum())}/{int(q[f'{tag}_n'].sum())}")
    if "jev_wait" in q:
        macro(f"Tr{S}WaitPct", 100 * (q.jev_wait / q.jev_T).mean(), 0)
if gv is not None:
    for tag, K in (("govr", "GovRules"), ("govj", "GovJev"), ("govja", "GovJeva")):
        rat = (t[tag] / t.default).dropna()
        macro(f"Tr{K}Lo", rat.min()); macro(f"Tr{K}Hi", rat.max()); macro(f"Tr{K}Gm", gmean(rat))
        macro(f"Tr{K}Done", f"{int(t[f'{tag}_done'].sum())}/{int(t[f'{tag}_n'].sum())}")
        macro(f"Tr{K}Clean", f"{int(t[f'{tag}_clean'].sum())}/{int(t[f'{tag}_n'].sum())}")
        macro(f"Tr{K}TGm", gmean((t[f"{tag}_T"] / t.gov_default_T).dropna()))
    ok = t[t.jev.notna()]                      # conditions on which controller T succeeded: like-for-like ratio
    macro("TrGovVsT", gmean(ok.govj / ok.jev)); macro("TrGovRulesVsT", gmean(ok.govr / ok.heur))
    g = gv[gv.kind.str.startswith("gov")]
    macro("TrGovRuns", len(g), 0); macro("TrGovCalls", int(g.model_calls.sum()), 0)
    macro("TrGovSlow", int(g.slow_capped_steps.sum()), 0); macro("TrGovUnstable", int(g.unstable_steps.sum()), 0)
    macro("TrGovErrHi", 100 * g.u_err_l2.max(), 2)
    hard = t.set_index("case").loc["cavT_Re400_dt10"]
    macro("TrGovHardIt", hard.govj, 1); macro("TrGovHardSlow", hard.govj_slow, 0); macro("TrGovHardDefIt", hard.default, 1)
    crash = t.set_index("case").loc["cav3T_Re400_dt30"]
    macro("TrGovCrashCaseIt", crash.govj, 1); macro("TrGovCrashCaseDef", crash.default, 1)
macro("TrNCond", len(t), 0); macro("TrNRuns", len(d), 0)
macro("TrGridCrashPct", 100 * t.grid_crashed.sum() / t.grid_n.sum(), 0)
macro("TrCoLo", t.co.min(), 0); macro("TrCoHi", t.co.max(), 0)
macro("TrDefLo", t.default.min(), 0); macro("TrDefHi", t.default.max(), 0)
same = 0; tot = 0
for c in t.case:
    a = d[(d.template == c) & d.group.isin(["t_design", "t_held"])]
    h = a[a.kind == "heurT"]; j = a[a.kind == "jevT"]
    if len(h) and len(j):
        tot += len(j); same += int((j.path.fillna("") == h.path.fillna("").iloc[0]).sum())
macro("TrSamePath", f"{same}/{tot}")
if "jev_unconv" in t:
    macro("TrUnconvPct", 100 * t.jev_unconv.sum() / t.steps.sum(), 1)
for col, K in (("default_err", "Def"), ("best_err", "Best"), ("jev_err", "Jev"), ("heur_err", "Heur")):
    if col in t and t[col].notna().any():
        macro(f"TrErr{K}Hi", 100 * t[col].max(), 2)
macro("TrCalls", int(d[d.kind.str.startswith("jevT")].model_calls.sum()), 0)
write_numbers(out / "numbers_transient.csv", M, "make_transient.py")
print(t.round(2).to_string()); print(open(out / "numbers_transient.csv").read())
