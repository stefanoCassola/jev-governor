# jevGovernor

[![CI](https://github.com/stefanoCassola/jev-governor/actions/workflows/ci.yml/badge.svg)](https://github.com/stefanoCassola/jev-governor/actions/workflows/ci.yml)

An in-loop governor for OpenFOAM solvers: steady SIMPLE-family runs (below) and, since 0.3.0, the
outer loop of transient PIMPLE runs ([Transient mode](#transient-pimple-mode)). Every *N* iterations a function object
hands the residual history to a small decision process, gets one bounded decision back and
applies it before the next iteration:

- under-relaxation factors (momentum by judgment, pressure and turbulence by fixed rules),
- a temporary first-order upwind fallback when the run still diverges at the lowest factor,
- nothing at all when the run is fine, converged or sitting on a residual plateau.

The judgment can come from [Jev](https://docs.typesafe.ai/introduction), TypeSafe's System One
model (typed yes/no probabilities in one request instead of generated text), or from an
offline rule set that uses the same features. The control law itself is ordinary code.

```
 simpleFoam ── jevGovernor function object ──  state_<n>.json  ──▶  jev-governor sidecar
     ▲            (libjevGovernor.so)                                 features ─▶ judge (jev | rules)
     └──── rewrite + explicit re-read of  ◀──   decision_<n>   ◀──   policy: one bounded move
           fvSolution / fvSchemes
```

Status: 0.3.1, the first public release. Built and tested with OpenFOAM v2312 and v2406 (openfoam.com), serial and
parallel. All benchmark numbers in this README were produced with 0.3.0 or earlier; 0.3.1 differs from 0.3.0 only in the handling of a missing or rejected API key and in the
documentation, the control code is identical.
Both judges are benchmarked below, `jev` against the live API (`jev-1.13.0`). Short version: the
governor removes the sensitivity to a badly chosen relaxation factor and, in transient runs, needs
about 0.6 of the default's outer iterations; it does not beat the best fixed settings, and **Jev is
not better than the offline rules** on any case measured, and costs latency. See
[Transient benchmark](#transient-benchmark) and [Results](#results).

## Install

This directory is the tool inside the [jevGovernor repository](../README.md); the study that the
design comes from is in [`../study`](../study), and [`../docs/RELATION.md`](../docs/RELATION.md)
says what each covers. All commands below run from `governor/`.

```sh
source /usr/lib/openfoam/openfoam2406/etc/bashrc   # your OpenFOAM environment
cd governor
./Allwmake                                         # builds libjevGovernor.so into $FOAM_USER_LIBBIN
                                                   # (cleans first if another OpenFOAM version built here)
pip install .                                      # sidecar, rules backend (no dependencies)
pip install '.[jev]'                               # adds the TypeSafe SDK for the jev backend
```

The sidecar alone, without cloning (the function object still has to be built from a clone):

```sh
pip install "git+https://github.com/stefanoCassola/jev-governor#subdirectory=governor"
```

The `rules` backend needs no key, no account and no network. For the `jev` backend you need a TypeSafe
account: create an API key in the dashboard at <https://console.typesafe.ai/keys> (see the
[TypeSafe quickstart](https://docs.typesafe.ai/introduction/quickstart)), export it as
`TYPESAFE_API_KEY` in the shell that starts the solver (or the sidecar, if you start it yourself), and
check it:

```sh
export TYPESAFE_API_KEY=...   # never put the key into a case file or the repository
jev-governor check            # one request: probabilities, latency, token count
```

**If the key is missing or rejected, the solver is not held up.** With `backend jev` and no
`TYPESAFE_API_KEY` (or an empty one, or no `typesafe-sdk`), or when the API rejects the key (HTTP 401 or
403), the sidecar answers every hand-off at once with "hold": the factors stay as they are, the run
continues ungoverned, and every decision line in the solver log and in `jevGovernor/sidecar.log` reads
`backend unavailable, run is NOT governed: TYPESAFE_API_KEY ...` with what to do. A rejected key is asked
once and not retried. Everything that waiting can cure is retried instead (rate limits, exhausted
credits, outages; see "API failures" below). `jev-governor check` exits with status 2 and the same
message. The key itself is never written to any file.

## Use

Add the function object to `system/controlDict` and run the solver as usual. The function object
starts and stops the sidecar itself.

```cpp
functions
{
    jevGovernor
    {
        type            jevGovernor;
        libs            (jevGovernor);

        interval        25;          // iterations between decisions
        mode            sync;        // sync: solver waits for the decision | async: never waits
        backend         rules;       // rules | jev   (env JEV_GOVERNOR_BACKEND overrides)
        fields          (U p k epsilon);   // residuals to watch
        momentum        U;           // governed equation
        followers       (k epsilon); // equations that make the same move
        pressure        p;           // field relaxed by the SIMPLE rule 1 - alpha_U
        bounds          (0.3 0.95);  // limits of the momentum factor
        upwindFallback  yes;
        description     "Steady incompressible flow over a backward-facing step, k-epsilon.";
    }
}
```

| entry | default | meaning |
|---|---|---|
| `interval` | 25 | iterations between decisions |
| `mode` | `sync` | `async` never blocks; a decision is applied when it arrives (some iterations late) |
| `backend` | `rules` | `jev` needs `TYPESAFE_API_KEY`; `model` selects the Jev version (default `jev-1.13.0`, pinned on purpose) |
| `fields` | momentum, pressure, followers | monitored residuals; targets come from `SIMPLE.residualControl` |
| `bounds` | `(0.3 0.95)` | the momentum factor never leaves this range |
| `upwindFallback` | `yes` | allow the temporary switch of `div(phi,...)` schemes to `upwind` |
| `launchSidecar` | `yes` | `no` if you start `jev-governor serve --case .` yourself (cluster front-end, container) |
| `timeout` | 120 | seconds a sync hand-off waits before holding the current settings (0: for ever) |
| `restoreOnEnd` | `yes` | put the original `fvSolution` / `fvSchemes` back at the end of the run |

After the run:

```sh
jev-governor report --case . -v      # decisions, guards, latency, whether upwind was active at the end
```

Every decision, with the state that was sent and the probabilities that came back, is in
`jevGovernor/decisions.jsonl`. `jev-governor decide --backend rules jevGovernor/state_*.json`
replays a run with another judge.

Try it: `tutorials/pitzDaily/Allrun` (rules) or `tutorials/pitzDaily/Allrun jev`.

Complete output of two real runs (steady and PIMPLE, `jev` backend), with every file explained, is in
[`docs/examples`](docs/examples). Note that the model's probabilities appear only in
`jevGovernor/decisions.jsonl`, together with the sentences the model was shown; the solver log only
says what was decided.

## How it works

**Hand-off.** The function object writes `jevGovernor/state_<n>.json` (residual samples since the
last decision, current factors, targets, settings). The sidecar answers with `decision_<n>`, an
OpenFOAM dictionary. Both sides write to a temporary name and rename, so a visible file is complete.
In parallel only the master talks to the sidecar; the decision is broadcast.

**Applying a decision.** The function object rewrites `system/fvSolution` / `system/fvSchemes`
atomically from the in-memory dictionaries and calls `fvSolution::read()` / `fvSchemes::read()`
on all ranks. It does not rely on `runTimeModifiable`: time-stamp based re-reading misses updates
at high iteration rates. The originals are kept as `<file>.jevGovernor.orig` and restored at the
end; if a run is killed, the next run (or `Allclean`) restores them first. A rewritten file has
its macros expanded and comments removed, which is why the original is put back.

**Features.** Jev reads text, not ordered quantities, and is documented to be weak at numeric
comparison. So all numeric work is code: least-squares slopes of log residuals over a window that
skips the 5 iterations after each change (counted from the iteration at which the function object
re-read the files, which it reports in the state), an amplitude-aware oscillation measure, distance to
target, iterations since the last new low. The judge sees words ("p: falling slowly, smooth, one to
two orders of magnitude above its target"), not numbers.

**Judgment.** Four independent yes/no questions in one request: *diverging?*, *safe to
accelerate?*, *stagnating?*, *stuck at a high factor?* The `rules` judge answers the same four
questions from thresholds.

**Policy (code, identical for both judges).** Probabilities map to one of five moves, multiplicative
in tau = a/(1-a). Pressure follows the SIMPLE rule 1 - alpha_U (left alone for SIMPLEC,
`consistent yes`). Cooldown of three decisions after a decrease. Hard guard independent of the
judge: non-finite residual or a tenfold rise since the last decision forces a large decrease and
sets a ceiling. A stall at a high factor backs off and sets a ceiling that expires after 300
iterations. After 300 iterations (or 12 intervals) without a new residual low the governor returns
to the best factor it has seen and freezes; the guard stays armed. The upwind fallback is used only
when the run still diverges at the lowest factor; it is lifted after four calm decisions, and made
permanent for the run if lifting it brings the divergence back.

**Async mode.** Decisions arrive late, so the sidecar decides on the newest state only (older ones
still contribute their residual samples), and only one change is in flight at a time: until a state
shows that the last change has reached the solver, the governor holds instead of repeating a move
from a stale factor. The guard is not held back. The function object applies every decision that
arrived, oldest first, so a later "hold" cannot swallow an earlier change.

**API failures.** The sidecar never skips a decision: it retries with back-off (up to 5 min
between attempts). In sync mode the solver sleeps meanwhile (no CPU time); in async mode it keeps
its current settings.

**What leaves your machine with the `jev` backend:** the verbal state only, i.e. the `description`
string you wrote, the momentum factor and sentences about residual trends. No mesh, geometry,
fields or file names.

## Transient (PIMPLE) mode

For `pimpleFoam`-type runs with a large fixed time step and `nOuterCorrectors` > 1, where every time
step needs several under-relaxed outer iterations that end when `PIMPLE.residualControl` is met or at
the cap. The cost to minimise is the number of outer iterations per time step.

```cpp
jevGovernor
{
    type            jevGovernor;
    libs            (jevGovernor);
    algorithm       PIMPLE;      // default when PIMPLE.nOuterCorrectors > 1
    interval        10;          // time steps between decisions
    backend         rules;       // rules | jev
    mode            sync;        // sync | async (the guard is synchronous in both)
    bounds          (0.3 0.95);  // momentum factor
    pressureBounds  (0.1 0.9);   // pressure factor
    floor           3;           // stop tuning at this many outer iterations per step
    checkpoint      no;          // yes: write a time directory before every trial increase
}
```

What differs from the steady mode:

- **Two factors.** The non-final factors of `U` (equation) and `p` (field) are governed by a coordinate
  search, one at a time; in PIMPLE the pressure factor matters as much as the momentum factor and the
  rule 1 - alpha_U is far from optimal. `UFinal`, `pFinal` and turbulence are never touched.
- **Every increase is a trial.** It is judged at the next decision and taken back, with a cap on that
  factor that expires, if it did not pay off. Near 0.85 and for the pressure factor only small probes
  (x1.2 in tau) are made.
- **The guard runs in the function object after every time step**, without the sidecar, in every mode.
  It reads the residual of every outer iteration from the solver-performance data of the step (no log
  parsing) and classifies the step: converged, **slow-capped** (hit the cap while the residuals fell
  monotonically) or **unstable** (rising or erratic residuals, non-finite values). Unstable steps take
  an open trial back at once, or lower both factors if there is none. Slow steps never lower the
  factors, because more under-relaxation only slows a slow loop; a trial that leaves the loop
  slow-capped is taken back after two settling steps.
- **Cliffs.** The stable optimum of a PIMPLE outer loop sits next to a crash cliff. A trial that ended
  in an unstable step leaves a permanent cliff for that factor (valid while the other factor is at
  least as aggressive); later trials approach it by bisection in tau and stop within 10 %.
- **What the guard cannot do:** it acts between time steps. A trial far beyond the cliff can kill the
  solver within a single step. With `checkpoint yes` a time directory is written before every trial;
  restart the solver from it (`startFrom latestTime`) and the governor reads the cliffs of the dead run
  from `jevGovernor/memory.json`, so it does not repeat the trial. The restarted run begins again from
  the original factors of `fvSolution` and continues `steps.csv`. There is no automatic restart.
- Output per run: `jevGovernor/steps.csv` (step, time, n_outer, class, aU, ap),
  `jevGovernor/decisions.jsonl` and `jevGovernor/summary.json` (completed, totals, final factors).

On a cluster where the solver runs in a container without Python, set `launchSidecar no` and start
`jev-governor serve --case <run> --idle-timeout 600` from any Python >= 3.9 on the shared file system
before the solver. The sidecar exits when the run ends, when the solver's PID disappears (same host)
or after the idle timeout.

Try it: `tutorials/cavityPimple/Allrun` (lid-driven cavity, Re 1000, 64 x 64 cells, Courant number 6).

### Transient benchmark

Run on a cluster by the study in [`../study`](../study) with governor 0.3.0 (OpenFOAM
v2312, sidecar outside the solver's container, `checkpoint no`): the study's 16 transient conditions
(lid-driven cavity 2-D and 3-D, cylinder vortex shedding, turbulent pitzDaily; Courant numbers 3 to 13).
Mean outer iterations per time step; `jev` values are means of three repeats:

| condition | default 0.7/0.3 | best fixed pair | governed, rules | governed, jev sync | governed, jev async |
|---|---|---|---|---|---|
| cavT_Re1000_dt05 | 23.5 | 14.4 (0.9/0.5) | 15.5 | 15.5 | 15.9 |
| cavT_Re1000_dt10 | 28.3 | 19.8 (0.8/0.5) | 24.1 | 23.8 | 24.1 |
| cav3T_Re1000_dt15 | 25.3 | 12.2 (0.95/0.5) | 15.7 | 15.7 | 15.8 |
| cav3T_Re1000_dt30 | 29.1 | 18.1 (0.9/0.5) | 21.8 | 23.0 | 23.1 |
| cylT_Re100_dt05 | 20.1 | 7.7 (0.95/0.7) | 9.6 | 9.6 | 9.7 |
| cylT_Re100_dt10 | 21.1 | 11.0 (0.8/0.7) | 12.3 | 12.7 | 12.8 |
| pitzT_U10_dt1 | 22.2 | 9.2 (0.8/0.7) | 9.8 | 9.8 | 9.8 |
| pitzT_U10_dt2 | 23.2 | 11.1 (0.8/0.7) | 12.6 | 12.6 | 12.8 |
| cavT_Re400_dt10 | 33.6 | 20.1 (0.9/0.3) | 22.2 (10 slow-capped start-up steps) | 22.2 | 22.5 |
| cavT_Re2500_dt05 | 25.2 | 14.0 (0.8/0.7) | 15.4 | 15.7 | 15.8 |
| cav3T_Re400_dt30 | 29.0 | 19.2 (0.95/0.3) | 22.0 | 23.1 | 23.3 |
| cav3T_Re2000_dt15 | 26.0 | 12.3 (0.95/0.5) | 15.1 | 15.3 | 15.4 |
| cylT_Re150_dt05 | 20.1 | 8.1 (0.95/0.7) | 10.6 | 10.6 | 10.6 |
| cylT_Re60_dt10 | 20.6 | 9.8 (0.95/0.5) | 11.6 | 11.6 | 11.7 |
| pitzT_U20_dt1 | 23.0 | 10.6 (0.8/0.7) | 11.8 | 11.8 | 11.9 |
| pitzT_U5_dt2 | 22.1 | 9.4 (0.8/0.7) | 10.6 | 10.6 | 10.7 |

- All 112 governed runs completed: no crash, and one unstable step in total (pitzT_U20, one async
  repeat), which the guard answered.
- The governed runs need 0.44 to 0.85 of the default's outer iterations, geometric mean 0.59 (rules),
  0.60 (jev sync and async). **Rules and Jev decide alike**; 4061 live Jev calls, no API error.
- It does **not reach the best fixed pair**: 17 % more iterations in the geometric mean. The best pair
  has to be found by a grid of runs, some of which crash (19 % of the study's fixed-pair runs did).
- Against the study's research controller T (rule-based twin) it needs 2 % more iterations where T
  succeeded, the price of the smaller probes, and it completes the two conditions on which T failed:
  cavT_Re400_dt10 (T read 25 slow-capped start-up steps as instability and spiralled to the floor; here
  10 steps are classified slow-capped, none unstable, 22.2 instead of 33.6 iterations per step) and
  cav3T_Re400_dt30 (T crashed after a trial beyond the cliff).
- **These conditions are not held out for the governor.** The transient mode was designed knowing all
  16 and T's two failures. They are a cross-check of a port, not a test of generalisation.
- The final fields differ from the references by at most 1.4 % (study metric).
- Data: `../study/data/results_t_governor.csv` (one row per run),
  `../study/data/derived/summary_transient.csv` (columns `govr`, `govj`, `govja`), run records in
  `../study/data/archives/transient_governor_run_records.tar.gz`.

Known limitation: the **coordinate search is path dependent**. It raises the momentum factor first;
with a judge that never takes a trial back it can end in a corner (high momentum factor, pressure
factor that cannot be raised) that is slower than the default. The rules and Jev avoided that on all
conditions above by taking unprofitable trials back, but the order of the search is not adapted to
the case. And because the iterations per step of an impulsively started flow fall over time anyway,
some early trials "pay off" for free.

### Tutorial

Measured locally on `tutorials/cavityPimple` (OpenFOAM v2406, serial, 200 time steps, sync; outer
iterations in total; the same tutorial run on the cluster, OpenFOAM v2312, gave the same 3342, see
`../study/data/derived/governor_cluster_smoke.json`):

| time step | default 0.7 / 0.3 | governed, rules | governed, jev | fixed pairs for reference |
|---|---|---|---|---|
| 0.1 (Co 6) | 4969 | 3342 | 3313 | |
| 0.4 (Co 26) | 6570, 7 steps capped | 6171, 6 steps slow-capped (start-up) | not run | 0.8/0.6: 6342; 0.93/0.3: 9031; 0.9/0.6: crashed in step 2 |

## Results

Measured with `benchmarks/run_benchmarks.sh` (OpenFOAM v2406, interval 25, sync mode, serial, two
stock tutorials, 1500 / 2000 iterations at most). "Fixed" keeps the starting factor for the whole
run. Entries are iterations to convergence, or for runs that do not converge the residual level at
the end in decades above the `residualControl` target (lower is better).

| case | start factor | fixed | governed, rules | governed, jev | wall [s] fixed / rules / jev |
|---|---|---|---|---|---|
| pitzDaily | 0.5 | 0.43 decades above | 341 | 341 | 14.2 / 4.7 / 8.3 |
| pitzDaily | 0.7 | 793 | 273 | 274 | 8.6 / 4.1 / 7.1 |
| pitzDaily | 0.9 (tutorial default) | 281 | 209 | 209 | 4.1 / 3.6 / 5.7 |
| pitzDaily | 0.95 (best fixed) | 195 | 195 | 195 | 3.4 / 3.5 / 5.6 |
| pitzDaily | 0.99 | 1.74 decades above (stalls) | 179 | 179 | 24.2 / 3.5 / 5.5 |
| airFoil2D | 0.5 | 1.65 decades above | 2.01 | 2.16 | 15.2 / 19.6 / 40.6 |
| airFoil2D | 0.7 (tutorial default) | 2.09 | 2.26 | 2.60 | 19.4 / 19.0 / 37.8 |
| airFoil2D | 0.9 | 2.34 | 2.45 | 2.76 | 17.7 / 18.9 / 38.7 |

Jev, measured over 285 live decisions: mean latency 0.27 to 0.35 s per decision (four judgments in
one request, about 700 input tokens), maximum 0.69 s, no API errors. At $0.042 per million input
tokens a 1000-decision run costs about three cents.

Read this honestly:

- The governor makes a run **insensitive to a badly chosen starting factor**. On pitzDaily every
  start ends within 179 to 341 iterations, where fixed factors range from 195 to "never".
- It does **not beat the best fixed factor** (195 vs 195). If you already know the best factor for
  a case, you do not need it.
- **Jev is not better than the rules.** On pitzDaily the two judges are indistinguishable; on
  airFoil2D Jev ends 0.15 to 0.35 decades higher. Both use the same features and the same policy,
  so this compares the judgments only. `rules` is the default for that reason; `jev` is there to
  be tested on cases where thresholds fail.
- On a case that **plateaus above its targets** (airFoil2D never reaches 1e-5) the governor does not
  help and ends 0.1 to 0.5 decades higher than doing nothing, because it spends the early iterations
  accelerating. It then detects the plateau and freezes.
- **Latency matters on small cases.** These 2-D cases run at about 90 iterations per second, so a
  0.3 s judgment in sync mode costs as much as 27 iterations: Jev doubles the wall time of airFoil2D.
  Use `mode async` there: pitzDaily from 0.7 with Jev takes 274 iterations / 7.1 s in sync and
  368 iterations / 4.0 s in async mode (decisions land 20 to 30 iterations late, the first one after
  about 90 because the sidecar has to start and connect). On cases with seconds per iteration the
  latency is negligible and sync is the better mode.
- **Runs with the `jev` backend are not repeatable run to run.** Jev returns slightly different
  probabilities for identical input (five identical requests here gave 0.73 to 0.77 and 0.34 to 0.38
  for two of the four judgments). Near the policy thresholds 0.3 / 0.6 / 0.8 that can flip a move, so
  decision paths and iteration counts vary between repeats. Every `jev` number in this README is a
  single run. The `rules` backend is deterministic. The spread over repeated runs is quantified in
  [`../study/docs/REPEATABILITY.md`](../study/docs/REPEATABILITY.md).
- Two 2-D tutorials are not evidence for industrial cases.

## Limitations

- Steady mode: SIMPLE-family solvers (tested: `simpleFoam`). Transient mode: the PIMPLE outer loop
  with a fixed time step (tested: `pimpleFoam`, laminar). No time-step control, no `adjustTimeStep`
  interplay tested, single region, incompressible naming (`U`, `p`; other names via `momentum` /
  `pressure`).
- Transient mode needs the momentum predictor for the per-iteration momentum residuals; without it
  only pressure residuals are used. Sub-cycled or multi-solve fields per outer iteration beyond
  `nCorrectors` x (`nNonOrthogonalCorrectors` + 1) pressure solves are not recognised.
- Solvers or libraries that keep a private copy of `fvSolution` do not see the changes. Verify with
  the flip test in `tests/integration/run_all.sh` when you use another solver.
- The upwind fallback only touches explicit `div(phi,...)` entries, not `default`. **A run that
  ends with the fallback active is first-order accurate in convection.** The log and the report say
  so; do not use such a result without checking.
- Single region. `fields` that are not solved are ignored.
- Jev is a young model (1.13): no adversarial robustness, documented context sensitivity. The
  model version is pinned; re-benchmark before changing it.
- With the `jev` backend the solver needs outbound HTTPS from the node that runs rank 0 (or start
  the sidecar elsewhere on a shared file system with `launchSidecar no`).

## Tests

```sh
pip install -e '.[test]' && pytest                  # sidecar: no OpenFOAM, no network
tests/integration/run_all.sh                        # needs OpenFOAM: sync, flip test, async,
                                                    # parallel, recovery after kill -9
```

## Licence and credits

GPL-3.0-or-later, like OpenFOAM. The tutorial case is derived from the OpenFOAM `pitzDaily`
tutorial. The judgment/policy split (yes/no judgments, moves in tau, settle window, cooldown,
guard, expiring ceiling, never skipping a decision) follows the relaxation-control study in
[`../study`](../study). The two are separate code bases with different policy versions; results of
one are not results of the other.

This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the
OpenFOAM software via www.openfoam.com, and owner of the OPENFOAM® and OpenCFD® trade marks. It is
not affiliated with TypeSafe AI.
