# Changelog

The public repository starts at 0.3.1; earlier versions were developed privately and their tags are
not part of it.

## 0.3.1 (2026-09-21)

- First public release. All benchmark numbers in the README were produced with 0.3.0 or earlier;
  0.3.1 changes only the handling of API keys and the documentation, the control code is identical.
- Fixed: a key that the API rejects (HTTP 401/403) was retried for ever, which in sync mode made every
  hand-off wait for the `timeout`. It is now asked once; the run continues ungoverned with a loud
  "NOT governed" notice that names `TYPESAFE_API_KEY`. A missing or blank key and a missing SDK give the
  same notice. Rate limits, exhausted credits and outages are retried as before.
- `jev-governor check` prints that message and exits with status 2 instead of a traceback.
- README: where to get a key; the rules backend needs none. Tests for missing, blank and rejected keys
  (mocked client, no live call) and an integration test without a key.

## 0.3.0 (2026-09-21)

- New transient mode (`algorithm PIMPLE`): governs the non-final under-relaxation factors of the
  momentum equation and the pressure field in the PIMPLE outer loop; cost = outer iterations per time
  step. Two-factor coordinate search with trial increases, expiring caps, permanent cliffs approached
  by bisection, optional checkpoint before a trial, cliffs remembered across a restart.
- The per-step guard runs in the function object (C++), after every time step and in every mode. It
  reads the residual of every outer iteration from the solver-performance data and tells slow-capped
  steps (never a reason to relax more) from unstable ones.
- Sidecar: `--idle-timeout`, exit when the solver PID from the state file disappears, a stale `done`
  file of an earlier run is ignored, `jevGovernor/summary.json` and `steps.csv` for batch runners,
  PIMPLE section in `jev-governor report`. Works with typesafe-sdk responses via `answers`.
- `Allwmake` cleans first when the objects were built against another OpenFOAM version (both versions
  use the same object directory; mixing them crashed at run time).
- README: runs with the `jev` backend are not repeatable run to run (the model's probabilities vary
  at identical input); all `jev` numbers are single runs.
- `docs/examples`: complete output of one steady and one PIMPLE run with the live `jev` backend.
- README: cluster benchmark of the transient mode on the study's 16 conditions (112 runs, no crash;
  0.59 of the default's outer iterations; not held out for the governor).
- New tutorial `cavityPimple`; unit tests (including replays of the two documented failures of the
  research controller) and four integration tests for the transient mode.

## 0.2.0 (2026-09-20)

- The tool now lives in `governor/` of the unified jevGovernor repository, next to the study it
  grew out of (`study/`). Nothing inside the tool moved relative to this directory; CI runs from the
  top-level workflow with `working-directory: governor`. Install the sidecar from a git URL with
  `#subdirectory=governor`.
- The function object reports the iteration at which it last re-read the relaxation factors
  (`controls.last_change_applied_at` in the state file); the settle window counts from there instead
  of an estimate. States written by an older function object still work.

## 0.1.1 (2026-09-19)

- Fixed: async mode with a slow judge repeated the same move from a stale factor (found in the
  first live Jev run; the instant rules judge had hidden it). The sidecar now decides on the newest
  state only, one change is in flight at a time, and the function object applies all arrived
  decisions in order instead of only the newest.
- The function object passes backend and model to the sidecar, which loads the SDK before the
  first state arrives.
- README: live Jev benchmark (285 decisions) next to the rules benchmark.

## 0.1.0 (2026-09-19)

First release.

- `jevGovernor` function object for steady-state SIMPLE-family solvers (OpenFOAM v2312, v2406):
  file-based hand-off, atomic rewrite and explicit re-read of `fvSolution` / `fvSchemes`,
  sync and async mode, parallel runs, back-up and restore of the system files.
- `jev-governor` sidecar with three judges: `jev` (TypeSafe Jev, four yes/no judgments per
  decision), `rules` (offline thresholds on the same features) and `mock` (tests).
- Policy in code: multiplicative moves in tau, SIMPLE pressure rule, cooldown, hard guard,
  expiring ceiling, plateau settle-and-freeze, first-order upwind fallback with warning.
- pitzDaily tutorial, unit tests, OpenFOAM integration tests, benchmark script.
