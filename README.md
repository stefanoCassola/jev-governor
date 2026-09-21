# jevGovernor

In-loop control of under-relaxation in OpenFOAM, and the study that tested it.

| | | |
|---|---|---|
| [`governor/`](governor/) | **The tool.** A function object plus a small Python sidecar that adjusts the under-relaxation factors of steady SIMPLE-family solvers and, since 0.3.0, of the outer loop of transient PIMPLE runs while they run. The judgment comes from offline rules (default) or from [Jev](https://docs.typesafe.ai/introduction), TypeSafe's System One model. | install, configure, tutorial, tests: [`governor/README.md`](governor/README.md) |
| [`study/`](study/) | **The study.** A controlled comparison of runtime control with fixed factors, a rule-based twin and a reinforcement-learning policy: 22 steady conditions (12 held out), 16 transient conditions (8 held out), 7 production-size permeability runs. The frozen research controllers, case set-ups, every run result and every model decision, and the scripts that generate every table, figure and number. | [`study/README.md`](study/README.md), audit: [`study/docs/AUDIT.md`](study/docs/AUDIT.md), repeatability: [`study/docs/REPEATABILITY.md`](study/docs/REPEATABILITY.md) |
| [`docs/RELATION.md`](docs/RELATION.md) | How the two relate, what each covers and what it does not. | |

## The short, honest version

* Adapting the relaxation factors at runtime makes a run **insensitive to a badly chosen factor**. It does **not**
  beat the best fixed factor.
  *Study:* 0.2–0.6 of the default iterations on the benchmark families where it works, about what the best fixed
  pair achieves, without searching for it. *Governor benchmarks:* every pitzDaily start (0.5–0.99) converges in
  179–341 iterations where fixed factors range from 195 to "never"; from the tutorial default the gain is modest
  (209 instead of 281 iterations).
* **Rules with the same features are as good as the model** on most cases, and have no latency.
  *Study:* the model's judgments paid off on one benchmark family only (a turbulent step, where a stall had to be
  told from slow progress). *Governor benchmarks:* Jev equal to the rules on pitzDaily and 0.15–0.35 decades worse
  on airFoil2D; Jev never beat the rules. `rules` is therefore the governor's default backend.
* **The model is general, the controllers are not.** Jev is used untrained and unchanged everywhere; the control
  code around it is AI-written per problem class (the governor and the study's steady controllers for SIMPLE /
  SIMPLEC, a permeability-aware controller, a transient PIMPLE controller and the governor's transient mode). None
  was tested on a case family it had not seen.
* Robustness is the open problem: non-monotonic convergence landscapes defeat simple back-off rules, stopping rules
  that look at the recent history of a quantity of interest can stop falsely after a factor change, and in
  transient PIMPLE runs the optimum sits next to a crash cliff.

Numbers quoted for the governor come from `governor/benchmarks` (two stock tutorials, OpenFOAM v2406); numbers
quoted for the study come from `study/data` via `study/analysis` (generated into `study/results/numbers_*.csv`).
They are different cases and different policy versions and should not be mixed.

## Quick start (tool)

```sh
source /usr/lib/openfoam/openfoam2406/etc/bashrc
cd governor && ./Allwmake && pip install .        # add '.[jev]' for the Jev backend
tutorials/pitzDaily/Allrun                         # rules backend, no API key needed
```

## Reproduce the study's tables and figures

```sh
cd study && python3 -m venv .venv && .venv/bin/pip install numpy pandas matplotlib pyvista
./run_analysis.sh .venv/bin/python
```

## How this was made

AI systems had two roles here, and we describe both because the second goes well beyond editing.

* **As the object of study.** The judgment model (TypeSafe Jev, `jev-1.13.0`) is part of the method. Every one of
  its decisions, with the state it was shown and the probabilities it returned, is in `study/data`.
* **As the research assistant.** An AI coding agent (Claude, Anthropic, used through Claude Code) did most of the
  hands-on work under the author's direction: it wrote the controllers, the rule-based baseline, the case set-ups,
  the cluster scripts and the governor; planned, submitted and monitored the simulation campaigns on the author's
  cluster account; proposed the successive controller designs, the held-out protocol and the freezing of versions;
  and wrote all analysis scripts and figures. It also audited its own work: the corrections in
  [`study/docs/AUDIT.md`](study/docs/AUDIT.md) were found by agents instructed to check the study sceptically,
  including agents that had not seen the conclusions.

The author conceived the project, supplied the production problem, the in-house solver, the microstructure data,
the compute and the model access, and set the goals and constraints at each stage, among them the comparison with
reinforcement learning, counting total wall-clock time including every model call, rerunning every run affected by a
service outage, and the audit itself. The author is not affiliated with TypeSafe and used the model as a paying
customer through its public interface.

## Licence and citation

See [`LICENSING.md`](LICENSING.md) and [`CITATION.cff`](CITATION.cff).
