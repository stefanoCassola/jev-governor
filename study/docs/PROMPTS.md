# State and questions sent to the judgment model (steady controller, variants P and E)

One request carries four yes/no questions (TypeSafe `Noul`), each with an instruction and a criterion for
`true` and `false`. Verbatim from `controller/benchmarks/frozen_v4/ctl3.py`:

```python
Q_TEXT = {
    "diverging": ("Do `residuals` and `observations` show that the iteration is diverging or becoming "
                  "unstable, meaning residuals rising persistently or an oscillation that grows, rather than "
                  "normal convergence, slow convergence or stagnation?",
                  {"true": "The iteration is diverging or becoming unstable.",
                   "false": "The iteration is converging, converging slowly, or stagnating without instability."}),
    "safe_to_accelerate": ("Is the iteration stable enough that a larger pseudo-time step (a higher momentum "
                           "under-relaxation factor) would be safe: the slowest equation is falling or stagnating "
                           "without strong oscillation, and no equation is rising?",
                           {"true": "Stable enough to take a larger step.",
                            "false": "Not safe: something is rising or strongly oscillating."}),
    "stuck_high": ("Is the iteration stuck: no progress for a long time according to `observations`, "
                   "without diverging, even though the momentum factor has already been raised to a high value "
                   "or its limit? (A pseudo-time step that is too large can stall convergence.)",
                   {"true": "Stuck despite a high momentum factor.",
                    "false": "Not stuck, or the factor is not high."}),
    "stagnating": ("Is the slowest equation making little or no progress towards its convergence target "
                   "(stagnating or falling only slowly)?",
                   {"true": "Progress is slow or stalled.", "false": "Progress is steady or fast."}),
}
```

## Example state

Sent at iteration 130 of a run on the P-M step at 25 m/s (variant E). The model returned P(diverging)=0.04,
P(safe_to_accelerate)=0.48, P(stagnating)=0.74, P(stuck_high)=0.75; rule R3 applied (no new low for 96
iterations, alpha_U > 0.8, P(stuck_high) > 0.6): the factor was lowered to 0.889 and capped for 100 iterations.

```
solver: "Steady incompressible turbulent flow: two-dimensional turbulent flow
  over a backward-facing step (Driver and Seegmiller geometry, step height
  12.7 mm), RANS k-omega SST model, inlet velocity 25 m/s. Solved with OpenFOAM
  simpleFoam using SIMPLEC (consistent) pressure-velocity coupling; with
  SIMPLEC, pressure under-relaxation factors close to 1 are usually tolerated.
  The run stops once every equation residual is below its convergence target.
  Goal: reach convergence in as few iterations as possible without oscillation
  or divergence. The momentum (U) under-relaxation factor acts like a pseudo
  time step: larger values give faster progress but less stability. The
  turbulence equations' factor is held fixed. The pressure under-relaxation
  factor is set automatically from the momentum factor; only the momentum
  factor is decided."
momentum_factor: 0.918
residuals:
  slowest_equation: "U: falling steadily, smooth, one to two orders of
                     magnitude above its target"
  other_equations: ["p: already below target", "k: falling steadily, smooth",
                    "omega: already below target"]
observations:
  - "the momentum factor was increased 10 iterations ago; the iterations right
     after that change are excluded from this window"
  - "since that change the slowest residual converges slower"
  - "the overall residual level has not reached a new low for 96 iterations"
```

Every state and answer of every run is in `data/archives/benchmark_decision_records.tar.gz`.
The transient controller's questions are in `controller/transient/ctlt.py`.
