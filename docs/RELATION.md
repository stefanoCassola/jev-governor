# How `governor/` and `study/` relate

`study/` is the research code and data of the benchmark study; `governor/` is the packaged tool that grew out of the
same design. They share the division of labour (numerics and policy in code, four yes/no judgments from a judge,
moves in tau = a/(1-a), cooldown, cap, guard) but they are **separate code bases with different policy versions**.
Results of one are not results of the other.

| | `study/controller/benchmarks` (variants P, E) | `governor/` | `study/controller/permeability` | `study/controller/transient` (controller T) |
|---|---|---|---|---|
| Problem class | steady SIMPLE / SIMPLEC, residual criterion | steady SIMPLE-family (tested: `simpleFoam`); since 0.3.0 also the PIMPLE outer loop (`algorithm PIMPLE;`) | steady SIMPLEC with a permeability-based stopping rule | transient PIMPLE outer loops |
| Coupling to the solver | coded function object + file hand-off, driver script starts the solver | compiled function object `libjevGovernor.so` starts the sidecar itself; serial and parallel | log polling, `fvSolution` rewrite, solver patch for the stop switch | coded function object, hand-off after every time step |
| What is controlled | alpha_U by judgment; alpha_p by rule (1-alpha_U SIMPLE, 1 SIMPLEC) | alpha_U by judgment, follower equations (k, epsilon, nuTilda, as configured) make the same move; alpha_p by rule for SIMPLE, untouched for SIMPLEC; optional upwind fallback, only at the factor floor and flagged as first-order in the log and the report | alpha_U and alpha_p chosen by the model | alpha_U and alpha_p, coordinate search |
| Judge | Jev or rule-based twin | rules (default) or Jev | Jev | Jev or rule-based twin |
| Cap after a stall | permanent (P) / expires after max(10N,100) its (E) | ceiling after a stall or after a guard trip, expires after 300 iterations; returns to the best factor seen and freezes after max(12N, 300) iterations without a new low; guard stays armed | – | expiring with exponential back-off; per-step guard |
| Evidence | 1445 runs, 22 conditions (12 held out), comparison with fixed-factor grids, rules and DRL | 2 stock tutorials x 5/3 start factors, rules vs Jev | 7 cases of 14–18 M cells + fixed-factor controls | 612 runs, 16 conditions (8 held out) |
| Status | frozen with checksums; not maintained as a tool | maintained, tested in CI (OpenFOAM v2312, v2406) | frozen | frozen |

**What the governor does not cover** (and nothing written about it should imply it does): permeability-type stopping rules, and
in steady mode anything beyond the two tutorials it was benchmarked on.

**Transient mode (governor 0.3.0) vs the study's controller T.** The governor re-implements T's policy (two-factor
coordinate search, trials that are taken back, expiring caps) as new code and fixes T's two held-out failures: the
guard runs inside the function object after every time step, never lowers the factors for a step that hits the cap
with monotonically falling residuals (slow-capped, as opposed to unstable), and an unstable trial leaves a permanent
limit that later trials approach by bisection. On the study's 16 transient conditions (cluster benchmark,
`study/data/results_t_governor.csv`, 112 governed runs) all runs completed: 0.44-0.85 of the default outer
iterations (geometric mean 0.59 rules, 0.60 Jev), about 3 % more iterations than T where T succeeded, one unstable
step in total; the two conditions on which T failed now run at 22-23 iterations per step (defaults 29 and 34).
Caveat: the governor's transient mode was written knowing all 16 conditions and both failures, so none of them is
held out for it; rules and Jev decide alike there too.

**Lessons from the study that are relevant to the governor**

* count the settle window from the iteration at which a change was *applied* (study: 67–82 % of asynchronous
  decisions were otherwise made on a stale window; fixing it removed them but did not reduce iterations).
  Governor status: since 0.1.1 only one change is in flight at a time; since 0.2.0 the function object reports
  the applied iteration and the window counts from it;
* a solver or library that keeps a private copy of `fvSolution` silently ignores runtime changes – verify with a
  flip test (this caused one false permeability result in the study);
* history-based stopping rules must be suspended after a factor decrease;
* near a stability limit approach in small steps; a fixed factor at the limit can be as fast as any controller and
  silently wrong.
