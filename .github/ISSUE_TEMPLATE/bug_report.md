---
name: Problem with a governed run
about: The governor crashed, hung, did nothing, or made a run worse
labels: bug
---

**What happened, and what did you expect?**

**Environment**
- OpenFOAM version and flavour (e.g. openfoam.com v2406):
- Solver, serial or parallel:
- `jev-governor --version`:
- Backend (`rules` or `jev`) and mode (`sync` or `async`):

**Set-up** (paste as text)
- the `jevGovernor` entry of `system/controlDict`:
- `relaxationFactors` and the `SIMPLE` or `PIMPLE` part of `system/fvSolution`:

**Output**
- last 50 lines of the solver log:
- from the run's `jevGovernor/` folder: `sidecar.log`, and if you can, `decisions.jsonl`
  (`steps.csv` and `summary.json` for PIMPLE runs):

Never paste an API key. If `jev-governor check` prints an error, paste the message only.
This repository is maintained with the help of an AI agent; a first answer may come from it.
