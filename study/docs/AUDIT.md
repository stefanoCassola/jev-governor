# Audit of the study (2026-09-19)

Before the results were written up, the whole pipeline was audited: raw logs → result tables → analysis
scripts → every quoted number, plus an independent code review of the frozen controllers. This file lists
what was checked, what was found, and what was done about it. Nothing here changes a frozen controller or
reruns a model-controlled simulation; all corrections concern references, analysis and the reported claims.

## Checked and found correct

| Check | Result |
|---|---|
| Frozen controller files vs. `SHA256SUMS` (variant P = `frozen_v3`, variant E = `frozen_v4`, acknowledged async = `frozen_v4a`) | all match; P→E differs only in the cap TTL, E→ack only in the acknowledgement logic |
| Iteration count and convergence flag of **all 1445 benchmark runs**, re-derived from the raw solver logs with an independent parser (`tools/cluster/audit_logs.py`) | 1445/1445 agree. 46 runs are flagged `fatal` in the tables without a `FOAM FATAL` message: they crashed with a floating-point exception (non-zero exit code), which the tables count as failed — correct |
| Determinism of fixed-factor runs | the default run of every condition, repeated in 2–5 batches on different nodes, always gives the same iteration count |
| Held-out case descriptions sent to the model | contain the correct Reynolds number / velocity (no leftover text from the parent case) |
| No look-ahead | synchronous decisions use iterations ≤ n; asynchronous ones only completed iterations |
| Rule-based twin vs. model-based controller | identical features, moves, cooldown, cap, guard, pressure rule and hand-off |
| Permeability numbers (iterations, hours, ΔK) | reproduce from `data/perm_results.csv` |
| The two permeability rounds are separate runs | different jobs, nodes and time stamps; on two cases they took identical decisions and produced bit-identical fields |

## Findings and corrections

1. **Accuracy references of the turbulent cases had not converged (HIGH).** The old references were default-factor
   runs with targets ×10⁻³. On pitzDaily and the P–M step they never reach such targets, and on the P–M step at
   25 and 30 m/s the default factors do not converge at all. Consequence: an earlier version of the results reported "about 22 %"
   velocity error for every method at 25/30 m/s, which was an artefact. **Fix:** new long references with the best
   fixed pair (`runs/ref2`, 16 000 / 20 000 iterations, no model calls), cross-checked by a second long run with
   different factors (agreement 0.03 % pitzDaily, 0.1–0.4 % P–M). All errors of these two families were recomputed
   (`data/results_uerr_ref2.csv`, `analysis/make_accuracy.py`). pitzDaily numbers are unchanged; P–M errors at
   25/30 m/s are now 4–5 % for the controllers and 1.3–1.5 % for the best fixed pair.
2. **Wall-clock ratios in an earlier version of the results could not be reproduced (HIGH).** It quoted 0.70 / 0.54 / 0.58
   (sync / async / rules) for the P–M step; the tables behind it give 0.53 / 0.41 / 0.43. **Fix:** every reported
   number is now written by the analysis scripts (`results/numbers_*.csv`); nothing is typed by hand.
3. **Single-run wall-clock times are noisy (HIGH for time claims, none for iterations).** Batches ran on several
   nodes with 20 concurrent jobs; the identical default run differs by a factor 1.1–2.2 between batches. **Fix:**
   the reported time is a noise-reduced estimate, `T = n_it · median(time per iteration of that method on that case) +
   measured waiting + median start-up`, and differences below ~20 % are treated as not interpretable. Raw times
   stay in the CSVs.
4. **Best fixed pair: time taken from another batch / other pair (MEDIUM).** Fixed by the same estimate; the pair is
   now always the minimum-iteration pair.
5. **Best fixed pairs lie on the edge of the grids, controllers may exceed the grid (MEDIUM).** Now stated with
   the results and among the limitations.
6. **Mislabelled observation (MEDIUM).** `ctl3.py` records the direction of a change as "decreased" whenever α_U did
   not rise. On the P–M step the first decision changes only α_p (0.5 → 1 by the SIMPLEC rule), so the model is told
   of a decrease that did not happen until the next real change. The rule-based twin ignores this field. Not fixed
   (frozen); disclosed.
7. **N = 5 ablation defeats the settle window (MEDIUM).** With N = 5 the excluded iterations are the whole interval
   and the fallback window is exactly the post-change bump. This is now stated with the ablation.
8. **Method description incomplete (MEDIUM).** Now documented: p bounds, cooldown and stall-clock restart of rule R3, the two
   levels of the rule-based `diverging` test, turbulence-factor handling, the fact that a few numbers are sent to
   the model, the numerical state of the permeability controller.
9. **Asynchronous settle window counted from the decision, not the application (known).** Tested in the
   acknowledged-async experiment; re-computed here from the decision records (`analysis/stale_decisions.py`):
   67–82 % stale decisions without, 0 % with acknowledgement, no gain in iterations.
10. **Service outage during one batch of the first design (LOW).** In batch `v2` (first design + SIMPLE rule) the 18 model-controlled laminar-step runs each missed 23–28 of their 400–4000 decisions
    (HTTP 402, account out of credit; factors were held). All of them had been dithering at the lower bounds since
    their first decision and ended "not converged", as did the same runs of batch `main`, which had no outage.
    No other benchmark batch contains a failed call. (The `errors` column of the tables counts these records for
    the first-design controller only; the later controllers retry instead of skipping and record `api_retries`,
    of which there are none.) The permeability runs that were hit by the same outage were discarded and rerun
    before any analysis.
11. **Permeability controller context text (LOW).** States a U bound of 0.995 (code: 0.99) and "17.5 million cells"
    for every case, and mentions that SIMPLEC tolerates α_p near 1. Disclosed.

## New evidence added during the audit

* Field-level check of the permeability runs (`data/perm_fielddiff.csv`): L2 difference of the final 3D velocity
  field to the default run is 0.01–1.5 % for the patched-solver runs. The falsely stopped run differs by 49 % with
  local spikes of 240× the mean velocity, although its permeability is off by only 2.4 %.
* Residual and factor histories of selected runs (`data/histories/`), which show the mechanism behind each
  success and failure (`results/figures/fig_anatomy.png`, `fig_perm_anatomy.png`).

## Second pass: independent fact check of the reported results

An independent reviewer recomputed about 60 generated numbers from the raw CSVs (all matched) and checked the
hand-written statements against the decision logs. Corrections made as a result:

* The rule-based twin in **variant P** had been left out of the tables. It converged on all four laminar-step
  conditions it was run on (including Re = 800, 14 072 iterations) and on 3 of 6 P–M velocities; the sentence
  "nothing converged at Re = 800 except 3 fixed pairs" was wrong. `results/tables/tab_iters.md` now has a rules column for both variants.
* "405 against 603 for the best pair inside the 4×4 grid" mixed our number with Pawar & Maulik's; ours is 600
  (pair 0.9/0.3).
* Run narratives corrected against the decision logs (variant P on the P–M step ends at 0.80/0.85; the rule-based
  cavity run does back off; the guard fired six times in the model-based cavity run).
* Asynchronous delay is a median of 6–10 iterations (90th percentile ≤ 19), not "10–20".
* Claims qualified: the match with the trained RL policy needs the wider action space and shorter interval; only
  the second held-out set is untouched by every design decision of the final controller; "no single back-off rule"
  → "neither of the two rules we tested"; the accuracy discussion of the loose P–M criterion; the grid-search cost;
  the statement that the model's answers are "sampled".
* The tension between "choosing actions failed on the benchmarks" and "the action-choosing first-generation
  controller produced the permeability result" is now discussed explicitly.
* The permeability stopping rule tests the *signed* regression slope; the false stop happened at a slope of −0.005.
* **Missing control identified:** no fixed pair closer to one than 0.95/1.0 had been run on the permeability cases,
  although the controller spends most of each run at 0.98–0.99. Control runs with 0.98/1.0 and 0.99/1.0 (stock
  solver, no controller, same node load) were submitted on 2026-09-19 (jobs 23847286–91).
  Result (2026-09-20): 0.99/1.0 from a zero field diverged on 4 of 7 cases (cancelled by hand, marked
  `CANCELLED_DIVERGED`), stopped falsely on 2 (ΔK +3.8 % and −19 %) and converged on 1; 0.98/1.0 converged correctly
  on 6 of 7 cases and stopped falsely on 1 (ΔK −3.0 %), in the same total time as the controller (21.0 h vs 21.3 h).
  The claims were changed accordingly: on this problem the controller is not faster than the best fixed
  pair; it is safer and needs no scan. See `results/tables/tab_perm_all.md` and `data/derived/perm_fixed_scan.csv`.

## Transient (PIMPLE) extension, 2026-09-20

* Controller T was developed on eight design conditions and frozen (`controller/transient/frozen_T`, SHA256SUMS,
  `FROZEN_AT`) before the eight held-out conditions were run. Design-set runs made before the freeze were archived
  (`_t_prefreeze` in the run records) and repeated with the frozen code; only the repeated runs are reported.
* Design history kept in the records: a first asynchronous mode (guard acting on the log, two to three steps late)
  crashed on two design conditions and was replaced by background judgments with a synchronous per-step guard; a
  fifth case family (T-junction with a total-pressure inlet) was dropped because it diverged for every factor pair.
* Held-out outcome reported as is: 6 of 8 conditions succeed, 2 fail (same for the rule-based twin). No retuning.
* Success criterion for every transient run, fixed pair or controller: completed and at most 2 % of the time steps
  at the outer-iteration cap.

## Repeatability of the model-based controllers, 2026-09-21

* Question: are three repeats per condition enough, and what does the model's non-determinism do to the results?
  No new runs; analysis of the existing repeats and decision records (`analysis/make_repeatability.py`,
  [`REPEATABILITY.md`](REPEATABILITY.md)).
* Correction: the results summary said "many synchronous repeats nevertheless took identical decisions". This held for
  variant P (7 of 16 conditions) and controller T (15 of 16), but for the final variant E the synchronous repeats
  followed identical paths on none of the 22 conditions. The sentence was replaced by measured numbers.
* New evidence: at identical input and history the model returned identical probabilities in only 2 % of 12,064
  decisions (differences small, median 0.03). Synchronous repeats always agreed on convergence; iteration counts
  of variant E differed by a median of 8 % (range over mean), up to 78 % on pitzDaily. Three per-condition
  comparisons with the rule-based twin and two with the defaults lie within the spread of the repeats and are
  undecided; none of the conclusions depends on them.

## Governor 0.3.0 cross-check on the transient conditions, 2026-09-21

* jevGovernor 0.3.0 (transient mode, commit f761371) was built in the cluster's OpenFOAM v2312 image and run on all 16
  transient conditions with the study's runner conventions (`tools/cluster/govbench.py`, sidecar outside the container,
  checkpoint off, defaults 0.7/0.3): rules, three synchronous and three asynchronous Jev repeats, default rerun in the
  same batches (128 runs). All 112 governed runs completed; the two conditions on which controller T failed succeed.
* These conditions are NOT held out for the governor: its transient mode was designed after T's failures were known and
  described to its developer session. This is stated wherever these runs are quoted.

## Frozen files in the public release

For the public release one docstring line of `ctl3.py` was reworded in `frozen_v3`, `frozen_v4` and `frozen_v4a` (a
pointer to a document that is not in this repository now points to `results/tables/tab_first_design.md`); no code
line changed, and `SHA256SUMS` was updated for that file only. SHA-256 of `ctl3.py` as recorded at freeze time:

* `frozen_v3/ctl3.py`: `c1b13da715f23d1211762abf5943971704be8927d581762ca721f0f6bc07a4f3`
* `frozen_v4/ctl3.py`: `ee85dbdef97fe1e78d495d0b1f8e57dafb0a75d6dd5abc16a25fa71f79cc664f`
* `frozen_v4a/ctl3.py`: `19470d1fff2d64121d075023a382ff1c19e811b6d7b5b4c61a44338dbbd437c6`
