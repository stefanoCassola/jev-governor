"""Repeatability of the model-based controllers: how much three repeats of the same condition differ.

usage: make_repeatability.py <data_dir> <results_dir>
Input: data/results_all.csv + results_v4a.csv (steady benchmarks, variant P = jev3, variant E = jev4), data/results_t.csv
(transient controller T = jevT), and, if extracted, the decision records data/_records (benchmarks) and data/_records_t
(transient) with the model's probabilities for every decision.

Per condition and controller the three repeats are compared on
  outcome     all repeats succeed, all fail, or mixed (success: converged; transient: completed with at
              most 2 % of the steps capped),
  path        the sequence of factor changes (iteration and new factors) is identical in all repeats,
  iterations  spread of the cost over the successful repeats, (max - min) / mean.
In synchronous mode the solver is deterministic and waits for every answer, so the first decision at which two repeats
differ always has an identical input state: the difference comes from the model's answer alone. The records give the
model's four probabilities for such pairs of identical states, which measures the model's own non-determinism.
Output: data/derived/repeatability.csv, results/numbers_repeat.csv.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import load, write_numbers

data, out = Path(sys.argv[1]), Path(sys.argv[2])
M: dict[str, str] = {}


def macro(k, v, nd=0):
    assert k.isalpha(), k
    M[k] = v if isinstance(v, str) else f"{v:,.{nd}f}".replace(",", "{,}")


b = load(data)
b = b[b.group.isin(["v3", "v3held", "v4", "v4held2"])]
b["cost"], b["success"] = b.iterations.astype(float), b.ok
t = pd.read_csv(data / "results_t.csv")
t["kind"] = t["run"].str.split("__").str[1].str.replace(r"_r\d+$", "", regex=True)
t = t[t.group.isin(["t_design", "t_held"])].copy()
t["cost"] = t.outer_total / t.steps
t["success"] = t.completed.astype(bool) & (t.unconverged_steps <= 0.02 * t.steps_planned)
SETS = {("P", False): (b, "jev3", "_records"), ("P", True): (b, "jev3_async", "_records"),
        ("E", False): (b, "jev4", "_records"), ("E", True): (b, "jev4_async", "_records"),
        ("T", False): (t, "jevT", "_records_t"), ("T", True): (t, "jevT_async", "_records_t")}


def decisions(recdir: Path, group: str, run: str):
    f = recdir / group / run / "decisions.jsonl"
    return [json.loads(l) for l in open(f) if l.strip()] if f.exists() else None


rows, probs = [], []
for (ctl, asy), (d, kind, rec) in SETS.items():
    recdir = data / rec
    for c, q in d[d.kind == kind].groupby("template"):
        q = q.sort_values("run")
        ok = q[q.success]
        r = {"controller": ctl, "async": asy, "case": c, "n": len(q), "n_success": len(ok),
             "same_path": q.path.fillna("").nunique() == 1,
             "cost_mean": ok.cost.mean() if len(ok) else np.nan,
             "cost_spread": (ok.cost.max() - ok.cost.min()) / ok.cost.mean() if len(ok) > 1 else np.nan}
        r["outcome"] = "all" if len(ok) == len(q) else ("none" if not len(ok) else "mixed")
        if not asy and recdir.exists():
            # first decision at which the repeats differ; its state must be identical (deterministic solver)
            ds = [decisions(recdir, g, run) for g, run in zip(q.group, q.run)]
            if all(x is not None for x in ds):
                first = None
                for i in range(min(map(len, ds))):
                    if len({json.dumps(x[i]["to"], sort_keys=True) for x in ds}) > 1:
                        first = i; break
                r["first_diff_decision"] = first
                r["first_diff_iteration"] = ds[0][first]["iteration"] if first is not None else np.nan
                # every decision with identical input state and identical preceding decisions in two repeats: any
                # difference in the probabilities or in the move is the model's own non-determinism
                for i in range(min(map(len, ds))):
                    if any("state" not in x[i] for x in ds):            # code-only guard step, no model call
                        continue
                    st = [json.dumps(x[i]["state"], sort_keys=True) for x in ds]
                    for a_ in range(len(ds)):
                        for b_ in range(a_ + 1, len(ds)):
                            same_before = all(ds[a_][k]["to"] == ds[b_][k]["to"] for k in range(i))
                            if st[a_] == st[b_] and same_before:     # identical input and identical history
                                pa, pb = ds[a_][i]["probs"], ds[b_][i]["probs"]
                                probs.append({"controller": ctl, "case": c, "decision": i,
                                              "max_abs_dp": max(abs(pa[k] - pb[k]) for k in pa),
                                              "same_probs": pa == pb,
                                              "same_move": ds[a_][i]["to"] == ds[b_][i]["to"]})
        rows.append(r)
R = pd.DataFrame(rows)
(data / "derived").mkdir(exist_ok=True)
R.to_csv(data / "derived" / "repeatability.csv", index=False)
P = pd.DataFrame(probs)
if len(P):
    P.to_csv(data / "derived" / "repeatability_probs.csv", index=False)
print(R.round(3).to_string())

for ctl in "PET":
    for asy, A in ((False, "Sync"), (True, "Async")):
        q = R[(R.controller == ctl) & (R["async"] == asy)]
        K = f"Rep{ctl}{A}"
        macro(K + "Cond", len(q))
        macro(K + "Runs", int(q.n.sum()))
        macro(K + "Mixed", int((q.outcome == "mixed").sum()))
        macro(K + "SamePath", int(q.same_path.sum()))
        s = q.cost_spread.dropna()
        macro(K + "SpreadMed", 100 * s.median(), 0)
        macro(K + "SpreadMax", 100 * s.max(), 0)
        macro(K + "SpreadLeFive", int((s <= 0.05).sum()))
        macro(K + "SpreadN", len(s))
# all steady model runs (both variants), the synchronous/asynchronous comparison quoted in docs/REPEATABILITY.md
for asy, A in ((False, "Sync"), (True, "Async")):
    q = R[R.controller.isin(["P", "E"]) & (R["async"] == asy)]
    s = q.cost_spread.dropna()
    macro(f"RepSteady{A}Cond", len(q)); macro(f"RepSteady{A}Mixed", int((q.outcome == "mixed").sum()))
    macro(f"RepSteady{A}SamePath", int(q.same_path.sum()))
    macro(f"RepSteady{A}SpreadMed", 100 * s.median(), 0); macro(f"RepSteady{A}SpreadMax", 100 * s.max(), 0)
    macro(f"RepSteady{A}SpreadN", len(s))
if len(P):
    first = R[(~R["async"]) & R.first_diff_decision.notna()]
    macro("RepFirstDiffIt", first.first_diff_iteration.median(), 0)          # iteration of the first differing decision
    macro("RepFirstDiffN", len(first))
    macro("RepSameStatePairs", len(P))
    macro("RepSameStateSameProbsPct", 100 * P.same_probs.mean(), 0)
    macro("RepSameStateSameMovePct", 100 * P.same_move.mean(), 1)
    dp = P.max_abs_dp[~P.same_probs]
    macro("RepSameStateDpMed", dp.median(), 2); macro("RepSameStateDpMax", dp.max(), 2)

    for ctl in "PET":
        macro(f"RepFlip{ctl}Pct", 100 * (1 - P[P.controller == ctl].same_move.mean()), 1)

# are per-condition comparisons with the deterministic references resolved by three repeats? A comparison counts as
# unresolved when the reference lies inside the range (min..max) of the successful synchronous repeats.
s4 = pd.read_csv(data / "derived" / "summary_v4.csv")
s4 = s4[(s4.jev_conv == s4.jev_n) & (s4.jev_n > 1)]
for col, K in (("heur_it", "Rules"), ("oracle_it", "Best"), ("default_it", "Def")):
    q = s4[s4[col].notna()]
    macro(f"RepE{K}Cmp", len(q))
    macro(f"RepE{K}Inside", int(((q[col] >= q.jev_it_min) & (q[col] <= q.jev_it_max)).sum()))

write_numbers(out / "numbers_repeat.csv", M, "make_repeatability.py")
print(open(out / "numbers_repeat.csv").read())
