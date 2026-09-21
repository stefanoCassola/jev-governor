"""Benchmark tables (Markdown), derived CSVs and the named benchmark numbers (CSV).

usage: make_tables.py <data_dir> <results_dir>

Cost measures
  iterations   outer iterations to the case's convergence criterion (deterministic for fixed factors).
  time         noise-reduced wall-clock estimate  T = n_it * c(case, method) + t_wait + t_0 , where
               c is the median solver time per iteration over all runs of that method on that case (all
               batches and nodes), t_wait the measured time the solver waited for decisions and t_0 the
               median start-up time of all runs. Raw wall-clock times of single runs are kept in the CSVs;
               identical default runs differ by a factor 1.1-2.2 between nodes, so raw single-run ratios
               are not reliable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import (ALL, DESIGN, FAMILIES, HELD1, HELD2, LABEL, family, gmean, load, set_name, sort_key,
                    write_numbers, write_table)

data, res = Path(sys.argv[1]), Path(sys.argv[2])

(data / "derived").mkdir(exist_ok=True)
d = load(data)
T0 = float(d[d.group.isin(["v4", "v4held2"])].other_s.median())
NUMBERS: dict[str, str] = {}


def macro(name: str, value, nd: int | None = 0):
    assert name.isalpha(), name
    if isinstance(value, str):
        NUMBERS[name] = value
    else:
        NUMBERS[name] = f"{value:,.{nd}f}".replace(",", "{,}")


def fmt(x, nd=0):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "--"
    return f"{x:,.{nd}f}".replace(",", "{,}")


def grid_group(c):
    return "main" if c in DESIGN else ("v3held" if c in HELD1 else "v4held2")


def ctl_group(c, ver):
    if ver == 3:
        return "v3" if c in DESIGN else "v3held"
    return "v4held2" if c in HELD2 else "v4"


def spi_med(case, kinds):
    q = d[(d.template == case) & d.kind.isin(kinds) & (d.iterations > 0)]
    return float(q.spi.median()) if len(q) else float("nan")


def summarise(ver: int) -> pd.DataFrame:
    rows = []
    cases = ALL if ver == 4 else DESIGN + HELD1
    for c in sorted(cases, key=sort_key):
        r = {"case": c, "set": set_name(c), "family": family(c)}
        dflt = d[(d.template == c) & (d.kind == "default")]
        r["default_ok"] = bool(dflt.ok.all())
        r["default_it"] = float(dflt.iterations.iloc[0]) if r["default_ok"] else np.nan
        assert dflt.iterations.nunique() == 1, c          # fixed-factor runs are deterministic
        c_def = spi_med(c, ["default"])
        r["default_T"] = r["default_it"] * c_def + T0
        r["default_wall_min"], r["default_wall_max"], r["default_n"] = dflt.wall_s.min(), dflt.wall_s.max(), len(dflt)
        g = d[(d.template == c) & (d.group == grid_group(c)) & d.kind.str.startswith("grid")].copy()
        g["aU"] = g.kind.str.extract(r"U([\d.]+)_")[0].astype(float)
        gok = g[g.ok]
        r["grid_n"], r["grid_conv"] = len(g), len(gok)
        best = gok.sort_values(["iterations", "wall_s"]).iloc[0]
        r["oracle_it"], r["oracle_pair"] = float(best.iterations), best.kind.replace("grid_U", "").replace("_p", "/")
        u_def = float(dflt.final_U.iloc[0])
        ratio = g[g.aU == best.aU].spi.median() / g[np.isclose(g.aU, u_def)].spi.median()
        r["oracle_T"] = r["oracle_it"] * c_def * ratio + T0
        r["oracle_err"] = best.u_err_l2
        r["grid_T"] = float((g.iterations * g.spi.fillna(c_def)).sum() + T0 * len(g))
        r["default_err"] = float(dflt.u_err_l2.iloc[0])
        for tag, kind in (("heur", f"heur{ver}"), ("jev", f"jev{ver}"), ("jeva", f"jev{ver}_async")):
            q = d[(d.template == c) & (d.group == ctl_group(c, ver)) & (d.kind == kind)]
            ok = q[q.ok]
            r[f"{tag}_n"], r[f"{tag}_conv"] = len(q), len(ok)
            r[f"{tag}_it"] = float(ok.iterations.mean()) if len(ok) else np.nan
            r[f"{tag}_it_min"] = float(ok.iterations.min()) if len(ok) else np.nan
            r[f"{tag}_it_max"] = float(ok.iterations.max()) if len(ok) else np.nan
            r[f"{tag}_wait"] = float(ok.controller_wait_s.mean()) if len(ok) else np.nan
            r[f"{tag}_calls"] = float(ok.decisions.mean()) if len(ok) else np.nan
            r[f"{tag}_T"] = (r[f"{tag}_it"] * spi_med(c, [kind]) + r[f"{tag}_wait"] + T0) if len(ok) else np.nan
            r[f"{tag}_wall_raw"] = float(ok.wall_s.mean()) if len(ok) else np.nan
            r[f"{tag}_err"] = float(ok.u_err_l2.mean()) if len(ok) else np.nan
            r[f"{tag}_finalU_min"] = float(ok.final_U.min()) if len(ok) else np.nan
            r[f"{tag}_finalU_max"] = float(ok.final_U.max()) if len(ok) else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


s4, s3 = summarise(4), summarise(3)
s4.to_csv(data / "derived" / "summary_v4.csv", index=False)
s3.to_csv(data / "derived" / "summary_v3.csv", index=False)

# ------------------------------------------------------------------ Table: iterations (v4, with v3 for comparison)
s3i = s3.set_index("case")
lines = [r"\begin{tabular}{@{}llrrlrrrrrr@{}}", r"\toprule",
         r" & & & \multicolumn{2}{c}{Best fixed pair} & \multicolumn{4}{c}{Variant E (final)} & \multicolumn{2}{c}{Variant P} \\",
         r"\cmidrule(lr){4-5}\cmidrule(lr){6-9}\cmidrule(lr){10-11}",
         r"Case & Set & Default & it. & $\alpha_U/\alpha_p$ & rules & model sync & conv. & model async & rules & model sync \\", r"\midrule"]
prev = None
for _, r in s4.iterrows():
    if prev and r.family != prev:
        lines.append(r"\addlinespace")
    prev = r.family
    e = s3i.loc[r.case] if r.case in s3i.index else None
    if e is None:
        early, early_h = "", ""
    else:
        early = "n.c." if e.jev_conv == 0 else fmt(e.jev_it) + (f" ({int(e.jev_conv)}/{int(e.jev_n)})" if e.jev_conv < e.jev_n else "")
        early_h = fmt(e.heur_it) if e.heur_conv else "n.c."
    lines.append(" & ".join([
        LABEL[r.case], r.set, fmt(r.default_it) if r.default_ok else "n.c.", fmt(r.oracle_it), r.oracle_pair,
        fmt(r.heur_it) if r.heur_conv else "n.c.",
        fmt(r.jev_it) if r.jev_conv else "n.c.", f"{int(r.jev_conv)}/{int(r.jev_n)}",
        (fmt(r.jeva_it) + ("" if r.jeva_conv == r.jeva_n else f" ({int(r.jeva_conv)}/{int(r.jeva_n)})")) if r.jeva_conv else "n.c.",
        early_h, early]) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(res / "tables" / "tab_iters.md", lines)

# ------------------------------------------------------------------ Table: time
lines = [r"\begin{tabular}{@{}lrrrrrrr@{}}", r"\toprule",
         r"Case & Default & Best fixed & Grid search & Rules & Model sync & (of which waiting) & Model async \\", r"\midrule"]
prev = None
for _, r in s4.iterrows():
    if r.family == "lbfs":
        continue
    if prev and r.family != prev:
        lines.append(r"\addlinespace")
    prev = r.family
    lines.append(" & ".join([LABEL[r.case], fmt(r.default_T, 1) if r.default_ok else "n.c.", fmt(r.oracle_T, 1),
                             fmt(r.grid_T), fmt(r.heur_T, 1), fmt(r.jev_T, 1), fmt(r.jev_wait, 1),
                             fmt(r.jeva_T, 1)]) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(res / "tables" / "tab_time.md", lines)

# compact family-level version (per-case values stay in data/derived/summary_v4.csv)
lines = [r"\begin{tabular}{@{}lrrrrrr@{}}", r"\toprule",
         r"Family (conditions) & Default & Best fixed & Rules & Model sync & waiting & Model async \\", r"\midrule"]
for fam, lab in (("cavity", "Cavity"), ("pitzDaily", "pitzDaily"), ("pm_bfs", "P--M step")):
    q = s4[(s4.family == fam)]
    qd = q[q.default_ok]
    lines.append(f"{lab} ({len(q)})" + " & " + " & ".join([
        f"{qd.default_T.mean():.0f}~s", f"{gmean(qd.oracle_T / qd.default_T):.2f}", f"{gmean(qd.heur_T / qd.default_T):.2f}",
        f"{gmean(qd.jev_T / qd.default_T):.2f}", f"{100 * (q.jev_wait / q.jev_T).mean():.0f}\\%",
        f"{gmean(qd.jeva_T / qd.default_T):.2f}"]) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(res / "tables" / "tab_time_family.md", lines)

# ------------------------------------------------------------------ family-level numbers
FAM = {"cavity": "Cav", "lbfs": "Lbfs", "pitzDaily": "Pitz", "pm_bfs": "Pm"}
fam_rows = []
for fam, F in FAM.items():
    for ver, s, V in ((4, s4, "F"), (3, s3, "E")):          # F = final design, E = earlier design
        q = s[s.family == fam]
        both = q[q.default_ok]
        for tag, M in (("jev", "Jev"), ("jeva", "Jeva"), ("heur", "Heur")):
            conv, n = int(q[f"{tag}_conv"].sum()), int(q[f"{tag}_n"].sum())
            macro(f"{F}{V}{M}Conv", f"{conv}/{n}")
            rat = (both[f"{tag}_it"] / both.default_it).dropna()
            trat = (both[f"{tag}_T"] / both.default_T).dropna()
            fam_rows.append({"family": fam, "version": ver, "method": tag, "conv": conv, "n": n,
                             "it_ratio_min": rat.min(), "it_ratio_max": rat.max(), "it_ratio_gm": gmean(rat),
                             "T_ratio_gm": gmean(trat), "it_min": q[f"{tag}_it_min"].min(), "it_max": q[f"{tag}_it_max"].max(),
                             "it_mean": q[f"{tag}_it"].mean()})
            if len(rat):
                macro(f"{F}{V}{M}ItLo", rat.min(), 2); macro(f"{F}{V}{M}ItHi", rat.max(), 2)
                macro(f"{F}{V}{M}ItGm", gmean(rat), 2); macro(f"{F}{V}{M}TGm", gmean(trat), 2)
                macro(f"{F}{V}{M}RunLo", q[f"{tag}_it_min"].min()); macro(f"{F}{V}{M}RunHi", q[f"{tag}_it_max"].max())
                macro(f"{F}{V}{M}Mean", q[f"{tag}_it"].mean())
        if ver == 4:
            macro(f"{F}DefLo", both.default_it.min()); macro(f"{F}DefHi", both.default_it.max())
            macro(f"{F}OraLo", q.oracle_it.min()); macro(f"{F}OraHi", q.oracle_it.max())
            orat = (both.oracle_it / both.default_it)
            macro(f"{F}OraItGm", gmean(orat), 2); macro(f"{F}OraTGm", gmean(both.oracle_T / both.default_T), 2)
            macro(f"{F}GridCostLo", (both.grid_T / both.default_T).min()); macro(f"{F}GridCostHi", (both.grid_T / both.default_T).max())
            w = (q.jev_wait / q.jev_T).dropna()
            if len(w):
                macro(f"{F}WaitLo", 100 * w.min()); macro(f"{F}WaitHi", 100 * w.max())
pd.DataFrame(fam_rows).to_csv(data / "derived" / "family_summary.csv", index=False)
gc = (s4[s4.default_ok].grid_T / s4[s4.default_ok].default_T)
macro("GridCostLo", gc.min()); macro("GridCostHi", gc.max())
macro("StartupS", T0, 1)

# model latency and call counts
jv = d[d.group.isin(["v4", "v4held2"]) & (d.kind == "jev4")]
lat = jv.api_latency_s.sum() / jv.decisions.sum()
macro("LatencyS", lat, 2)
macro("NRunsTotal", len(d))
macro("NRunsModel", int(d.kind.str.startswith("jev").sum()))
macro("NCallsTotal", int(d[d.kind.str.startswith("jev")].decisions.sum()))

# timing noise of identical default runs
dd = d[d.kind == "default"].groupby("template").wall_s.agg(["min", "max", "size"])
dd = dd[dd["size"] >= 2]
macro("NoiseLo", (dd["max"] / dd["min"]).min(), 1); macro("NoiseHi", (dd["max"] / dd["min"]).max(), 1)

# Pawar-Maulik specifics
pm = s4[s4.family == "pm_bfs"]
macro("PmFinalULo", pm.jev_finalU_min.min(), 3); macro("PmFinalUHi", pm.jev_finalU_max.max(), 3)
macro("PmHeurLo", pm.heur_it.min()); macro("PmHeurHi", pm.heur_it.max())
macro("PmAsyncLo", pm.jeva_it_min.min()); macro("PmAsyncHi", pm.jeva_it_max.max())
macro("PmOraAdvPct", 100 * (1 - pm.oracle_it.mean() / pm.jev_it.mean()))

# ------------------------------------------------------------------ DRL comparison table (Pawar-Maulik test velocities)
def one(case, group, kind):
    q = d[(d.template == case) & (d.group == group) & (d.kind == kind)]
    return q.iloc[0] if len(q) else None


lines = [r"\begin{tabular}{@{}lccc@{}}", r"\toprule", r"Method & 25~m/s & 50~m/s & 75~m/s \\", r"\midrule"]
tv = ["pm_bfs_U25", "pm_bfs_U50", "pm_bfs_U75"]
s4i = s4.set_index("case")


def row(label, vals):
    lines.append(label + " & " + " & ".join(vals) + r" \\")


row("Template factors 0.5/0.5", [fmt(s4i.loc[c].default_it) if s4i.loc[c].default_ok else "n.c." for c in tv])
g99 = [one(c, "main", "grid_U0.9_p0.9") for c in tv]
row(r"Best pair of the $4\times4$ grid of Pawar and Maulik (2021) (0.9/0.9)", [fmt(float(x.iterations)) if x is not None and x.ok else "n.c." for x in g99])
row(r"Best pair of the extended grid (0.98/0.9)", [fmt(s4i.loc[c].oracle_it) for c in tv])
lines.append(r"PPO policy, 2000 training episodes (Pawar and Maulik 2021) & \multicolumn{3}{c}{$\approx$500 (read from their Fig.~9)} \\")
row("Rule-based controller (single run)", [fmt(s4i.loc[c].heur_it) for c in tv])
row(r"Judgment model, $N=10$, own bounds (mean of 3)", [fmt(s4i.loc[c].jev_it) for c in tv])
b10 = [one(c, "v4", "jev4_pmb_i10") for c in tv]; b100 = [one(c, "v4", "jev4_pmb_i100") for c in tv]
row(r"Judgment model, $N=10$, DRL bounds $[0.2,0.95]$ (single run)", [fmt(float(x.iterations)) if x.ok else "n.c." for x in b10])
row(r"Judgment model, $N=100$, DRL bounds (single run)", [fmt(float(x.iterations)) if x.ok else "n.c." for x in b100])
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(res / "tables" / "tab_drl.md", lines)
macro("DrlTenLo", min(x.iterations for x in b10)); macro("DrlTenHi", max(x.iterations for x in b10))
macro("DrlHundLo", min(x.iterations for x in b100)); macro("DrlHundHi", max(x.iterations for x in b100))
macro("DrlOwnLo", min(s4i.loc[c].jev_it for c in tv)); macro("DrlOwnHi", max(s4i.loc[c].jev_it for c in tv))
macro("DrlOraLo", min(s4i.loc[c].oracle_it for c in tv)); macro("DrlOraHi", max(s4i.loc[c].oracle_it for c in tv))

# ------------------------------------------------------------------ replication of the constant-factor sweep at 44.2 m/s
PM_FIG7 = {(0.3, 0.3): 885, (0.5, 0.3): 1914, (0.7, 0.3): None, (0.9, 0.3): 705,
           (0.3, 0.5): 881, (0.5, 0.5): 1903, (0.7, 0.5): None, (0.9, 0.5): 653,
           (0.3, 0.7): 2201, (0.5, 0.7): 1529, (0.7, 0.7): None, (0.9, 0.7): 725,
           (0.3, 0.9): 1417, (0.5, 0.9): 1014, (0.7, 0.9): 668, (0.9, 0.9): 603}
lines = [r"\begin{tabular}{@{}lcccc@{}}", r"\toprule", r"$\alpha_p$ \textbackslash{} $\alpha_U$ & 0.3 & 0.5 & 0.7 & 0.9 \\", r"\midrule"]
within, rep_rows = 0, []
for ap in (0.3, 0.5, 0.7, 0.9):
    cells = []
    for au in (0.3, 0.5, 0.7, 0.9):
        x = one("pm_bfs_U44", "main", f"grid_U{au}_p{ap}")
        ours = float(x.iterations) if x.ok else None
        theirs = PM_FIG7[(au, ap)]
        rep_rows.append({"alpha_U": au, "alpha_p": ap, "this_work": ours, "pawar_maulik_fig7": theirs})
        if (ours is None) == (theirs is None) and (ours is None or abs(ours - theirs) / theirs <= 0.02):
            within += 1
        cells.append(f"{fmt(ours) if ours else 'n.c.'} / {fmt(float(theirs)) if theirs else 'n.c.'}")
    lines.append(f"{ap} & " + " & ".join(cells) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(res / "tables" / "tab_replication.md", lines)
pd.DataFrame(rep_rows).to_csv(data / "derived" / "replication_pm44.csv", index=False)
macro("ReplWithin", within)
rp = pd.DataFrame(rep_rows).dropna(subset=["this_work"]).sort_values("this_work").iloc[0]
macro("ReplBestIt", rp.this_work); macro("ReplBestPair", f"{rp.alpha_U:g}/{rp.alpha_p:g}")
macro("ReplExtIt", float(one("pm_bfs_U44", "main", "grid_U0.98_p0.9").iterations))
macro("ReplOursNineNine", float(one("pm_bfs_U44", "main", "grid_U0.9_p0.9").iterations))

# ------------------------------------------------------------------ ablations of the earlier design (decision interval, low start)
ab = []
for c in DESIGN + HELD1:
    base = s3i.loc[c]
    for kind in ("jev3_i5", "jev3_i20", "jev3_low"):
        x = one(c, ctl_group(c, 3), kind)
        if x is not None and x.ok and base.default_ok:
            ab.append({"case": c, "family": family(c), "kind": kind, "it_ratio": x.iterations / base.default_it,
                       "vs_n10": x.iterations / base.jev_it if base.jev_conv else np.nan, "calls": x.decisions})
ab = pd.DataFrame(ab); ab.to_csv(data / "derived" / "ablation_v3.csv", index=False)
for kind, K in (("jev3_i5", "AblFive"), ("jev3_i20", "AblTwenty"), ("jev3_low", "AblLow")):
    q = ab[(ab.kind == kind) & (ab.family == "cavity")]
    macro(f"{K}CavLo", q.it_ratio.min(), 2); macro(f"{K}CavHi", q.it_ratio.max(), 2)
q = ab[ab.kind == "jev3_low"].vs_n10.dropna()
macro("AblLowVsLo", 100 * (q.min() - 1)); macro("AblLowVsHi", 100 * (q.max() - 1))

# ------------------------------------------------------------------ first design (v1/v2) summary
v1 = []
for c in DESIGN:
    r = {"case": c}
    for grp, tag in (("main", "v1"), ("v2", "v2")):
        q = d[(d.template == c) & (d.group == grp) & (d.kind == "jev")]
        r[f"{tag}_conv"], r[f"{tag}_n"] = int(q.ok.sum()), len(q)
        r[f"{tag}_it"] = float(q[q.ok].iterations.mean()) if q.ok.any() else np.nan
        h = d[(d.template == c) & (d.group == grp) & (d.kind == "heuristic")]
        r[f"{tag}_heur"] = float(h.iterations.iloc[0]) if len(h) and h.ok.iloc[0] else np.nan
    v1.append(r)
v1 = pd.DataFrame(v1); v1.to_csv(data / "derived" / "summary_v1_v2.csv", index=False)
lines = [r"\begin{tabular}{@{}lrrrrrr@{}}", r"\toprule",
         r" & & & \multicolumn{2}{c}{First design} & \multicolumn{2}{c}{+ SIMPLE rule in code} \\",
         r"\cmidrule(lr){4-5}\cmidrule(lr){6-7}",
         r"Case & Default & Best fixed & rules & model (conv.) & rules & model (conv.) \\", r"\midrule"]
for _, r in v1.iterrows():
    b = s4i.loc[r.case]
    def cell(it, conv, n):
        return "--" if n == 0 else (f"{fmt(it)} ({conv}/{n})" if conv else f"n.c. (0/{n})")
    lines.append(" & ".join([LABEL[r.case], fmt(b.default_it) if b.default_ok else "n.c.", fmt(b.oracle_it),
                             fmt(r.v1_heur) if np.isfinite(r.v1_heur) else "n.c.", cell(r.v1_it, r.v1_conv, r.v1_n),
                             (fmt(r.v2_heur) if np.isfinite(r.v2_heur) else "n.c.") if r.v2_n else "--",
                             cell(r.v2_it, r.v2_conv, r.v2_n)]) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}"]
write_table(res / "tables" / "tab_first_design.md", lines)

# ------------------------------------------------------------------ acknowledged asynchronous variant (v4a) against its same-batch control
a = d[d.group.isin(["v4a", "v4aheld2"])]
va = []
for c in sorted(a.template.unique(), key=sort_key):
    q = a[a.template == c]
    ctl, ack = q[(q.kind == "jev4_async") & q.ok], q[(q.kind == "jev4ack_async") & q.ok]
    va.append({"case": c, "family": family(c), "ctl_conv": len(ctl), "ack_conv": len(ack),
               "ctl_it": ctl.iterations.mean(), "ack_it": ack.iterations.mean(),
               "ctl_it_sd": ctl.iterations.std(), "ack_it_sd": ack.iterations.std(),
               "ctl_calls": ctl.decisions.mean(), "ack_calls": ack.decisions.mean()})
va = pd.DataFrame(va); va.to_csv(data / "derived" / "v4a_compare.csv", index=False)
for fam, F in FAM.items():
    q = va[(va.family == fam) & (va.ctl_conv > 0) & (va.ack_conv > 0)]
    if len(q):
        macro(f"Ack{F}It", gmean(q.ack_it / q.ctl_it), 2)

import glob, json
rec = data / "_records"
if rec.exists():
    dl = {}
    for fam in ("cavity", "pitzDaily", "pm_bfs"):
        v = []
        for g in ("v4", "v4held2"):
            for dd_ in glob.glob(str(rec / g / f"{fam}*__jev4_async_r*")):
                R = json.load(open(dd_ + "/result.json")); app = sorted(R.get("async_applied_at") or [])
                for x in (json.loads(l) for l in open(dd_ + "/decisions.jsonl") if l.strip()):
                    if x["to"]["U"] != x["from"]["U"]:
                        ap_ = next((t for t in app if t >= x["iteration"]), None)
                        if ap_ is not None:
                            v.append(ap_ - x["iteration"])
        dl[fam] = (np.median(v), np.percentile(v, 90))
    macro("DelayMedLo", min(a for a, _ in dl.values())); macro("DelayMedHi", max(a for a, _ in dl.values()))
    macro("DelayNinetyHi", max(b for _, b in dl.values()))
second = va[(va.ctl_conv > 0)].merge(s4[["case", "jeva_it"]], on="case")
macro("AsyncRepeatSpread", 100 * (second.ctl_it / second.jeva_it - 1).abs().max())
macro("NRefRuns", int((d.group == "ref").sum()))

write_numbers(res / "numbers_bench.csv", NUMBERS, "make_tables.py")
print(open(res / "numbers_bench.csv").read())
