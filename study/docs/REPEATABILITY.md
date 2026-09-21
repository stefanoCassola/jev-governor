# Repeatability of the model-based controllers

The judgment model (Jev, fixed version, behind an API) is **not deterministic** and offers no seed. This note
explains what that means for the results, using only the runs of the study. We made no new runs for it: every
model-controlled condition was run three times in each mode. All numbers are produced by
[`analysis/make_repeatability.py`](../analysis/make_repeatability.py). The per-condition table is
[`data/derived/repeatability.csv`](../data/derived/repeatability.csv) and the per-decision comparison is
[`data/derived/repeatability_probs.csv`](../data/derived/repeatability_probs.csv). The top-level README summarises it.

## Short answer

- **Outcomes are repeatable, iteration counts are not.** In synchronous mode, all three repeats agreed on whether
  the run converged, on every steady and every transient condition. The number of iterations they needed did not
  agree.
- **Three repeats are enough for the qualitative conclusions and not for fine per-condition comparisons.** Three
  repeats settle which families the controller solves. They also settle the comparison with the best fixed pair:
  that pair lies outside the range of the repeats on every condition. They do not settle comparisons whose
  difference is smaller than the spread of the repeats (listed below). We report ranges, not confidence intervals,
  because three samples cannot support confidence intervals.
- **The non-determinism comes from the model, not the solver.** Fixed-factor runs gave identical iteration counts
  in up to five batches on different nodes. In synchronous mode the solver waits for every answer, so two repeats
  can only part at a model answer.

## 1. The model gives different answers to identical input

In synchronous repeats of the same condition, we compared the decisions that had an identical input state *and* an
identical history of all earlier decisions (12,064 such pairs of decisions). Any difference at these decisions is
the model's own.

| | pairs | identical probabilities | move changed |
|---|---:|---:|---:|
| Variant P | 8,889 | 1.6 % | 0.2 % |
| Variant E (final) | 1,251 | 1.8 % | 4.9 % |
| Controller T (transient) | 1,924 | 2.1 % | 0.2 % |

- The four probabilities are almost never bit-identical.
- The differences are small. The largest difference per decision has a median of 0.03 and a maximum of 0.16.
- A small difference changes the move only when a probability lies near one of the controller's thresholds
  (0.3 / 0.6 / 0.8).
- We cannot tell whether the variation comes from sampling or from serving (batching, hardware).

## 2. What that does to runs of three repeats

The "path" is the sequence of factor changes. The spread is the (max − min) / mean of the cost over successful
repeats; the cost is iterations for steady runs and outer iterations per time step for transient runs.

| Controller, mode | conditions | mixed outcome | identical path | median spread | max spread |
|---|---:|---:|---:|---:|---:|
| Variant P, sync | 16 | 0 | 7 | 2 % | 28 % |
| Variant P, async | 16 | **5** | 2 | 26 % | 50 % |
| Variant E, sync | 22 | 0 | **0** | 8 % | 78 % |
| Variant E, async | 22 | 0 | 0 | 17 % | 75 % |
| Controller T, sync | 16 | 0 | 15 | 0 % | 0 % |
| Controller T, async | 16 | 0 | 9 | 0 % | 1 % |

- **Synchronous repeats part early.** Where they part, the first differing decision comes at a median iteration
  of 55. From that point the solver states differ, and the paths drift apart.
- **Variant E is the least repeatable in path and iteration count.** Its synchronous repeats followed different
  paths on every condition. Its moves flip more often at identical input, probably because its probabilities
  often lie near a threshold. We did not verify that explanation.
- **The spread is largest on pitzDaily** (54–78 %). Runs there are only about 300 iterations long, so one early
  decision changes a large share of the run.
- **Asynchronous repeats also depend on timing.** A decision is applied when the answer arrives. Variant P
  disagreed on convergence on five P–M step conditions: 1 or 2 of 3 repeats converged. A second set of
  asynchronous repeats of variant E (acknowledged-async ablation, `results_v4a.csv`) differed from the first in its mean by up to
  50 % on single conditions.
- **Transient repeats barely differ in cost.** Their paths match the rule-based twin on 31 of 48 synchronous runs,
  and where they differ, the per-step cost barely changes.
- **Permeability runs.** All seven cases were run in two rounds, but round 2 used a patched solver, so the rounds
  are not strict repeats. On two cases (med_x, med_z) both rounds took identical decisions and gave bit-identical
  fields. On four more cases the rounds differed by 1–14 % in iterations:

  | case | round 1 | round 2 |
  |---|---:|---:|
  | base | 284 | 287 |
  | long_y | 297 | 289 |
  | med_y | 96 | 103 |
  | fvc56_x | 143 | 163 |

  The 90th-percentile case differs by design: round 1 stopped falsely (see `perm_results.csv`).

## 3. Which comparisons three repeats do not decide

Variant E, synchronous: a comparison is *undecided* when the deterministic reference lies inside the min–max range
of the three repeats.

| Reference | comparisons | undecided | conditions |
|---|---:|---:|---|
| Best fixed pair | 17 | 0 | — |
| Default factors | 15 | 2 | pitzDaily 10 m/s (282 vs 248–449), pitzDaily 15 m/s (290 vs 222–396) |
| Rule-based twin | 17 | 3 | cavity Re 100 (715 vs 712–744), cavity Re 250 (717 vs 667–756), pitzDaily 15 m/s (243 vs 222–396) |

None of these cases changes a conclusion:

- The conclusions say the controller gave no gain on pitzDaily, and it does not claim to beat the rule-based twin on
  the cavities.
- The family-level geometric means average over several conditions and three repeats. Their uncertainty is
  smaller than the per-condition spread, but we did not quantify it.

## 4. What to expect when repeating the study

- **Same model version:** expect the same outcomes (which conditions converge) and similar family-level ratios.
  Do not expect the same iteration counts, the same decision paths or the same numbers in `results/tables/tab_iters.md`.
- **Retired model version:** the runs cannot be repeated exactly. The complete record of states and answers is
  archived in `data/archives/` so that every decision can be inspected.
- **More repeats:** 10 or more per condition would allow confidence intervals on single conditions. We did not
  run them.
