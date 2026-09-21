# Example run output

What a governed run leaves behind, from two real runs of the tutorials in this repository. Nothing
here is hand-edited, except that absolute paths, the host name and the user name were replaced by
`<case>`, `<scratch>`, `<home>`, `<host>` and `<user>`.

| | |
|---|---|
| jevGovernor | 0.3.0 (before the first public release 0.3.1, which has the same control code) |
| OpenFOAM | v2406 (openfoam.com), serial |
| Judge | `jev` backend, model `jev-1.13.0`, live API, `mode sync` |
| Date | 2026-09-21 |

Runs with the `jev` backend are not repeatable run to run (see
[`../../../study/docs/REPEATABILITY.md`](../../../study/docs/REPEATABILITY.md)): the steady run below
took 286 iterations, the same set-up took 274 in the benchmark of the main README. With
`backend rules` the same files are produced, without `probs` from a model and without latency.

## `steady_pitzDaily_jev/`: steady mode

`tutorials/pitzDaily` with `backend jev`: `simpleFoam`, starts from a momentum factor of 0.7, converged
in 286 iterations (a fixed 0.7 needs 793), 11 decisions.

| file | what it is |
|---|---|
| `log.simpleFoam` | the full solver log. Look for the lines starting with `jevGovernor jevGovernor:`: the sidecar start, one line per decision with its note, the re-read relaxation factors, the summary at the end |
| `decisions.jsonl` | one JSON object per decision: the verbal `state` that was sent to the model, the four probabilities `probs` that came back, `latency_s`, `input_tokens`, the features behind the words, the move and the new factors |
| `state_100.json`, `decision_100` | one hand-off pair. The function object wrote the state after iteration 100 (residual samples since the last hand-off, current factors, targets, settings); the sidecar answered with the decision, an OpenFOAM dictionary that the function object applies |
| `sidecar.log` | the sidecar's own output, one line per decision |
| `report.txt` | output of `jev-governor report --case . -v` |

## `pimple_cavity_jev/`: transient (PIMPLE) mode

`tutorials/cavityPimple` with `backend jev`: `pimpleFoam`, 200 time steps, 3313 outer iterations in
total (the default factors 0.7 / 0.3 need 4969), 20 decisions, no guard action.

| file | what it is |
|---|---|
| `log.pimpleFoam.gz` | the full solver log (1.5 MB unpacked) |
| `log.pimpleFoam.excerpt.txt` | the start of that log up to the first time step, every governor line, time step 50 with its decision, and the end |
| `steps.csv` | one row per time step, written by the function object: outer iterations, class (`ok`, `slow_capped`, `unstable`) and the two factors in force |
| `summary.json` | totals for batch runners: completed, decisions, outer iterations, capped steps, final factors |
| `decisions.jsonl` | as above; in this mode also `guard_events`, the expiring caps `cap` and the permanent `cliffs` |
| `state_50.json`, `decision_50` | one hand-off pair. The state carries the residual of every outer iteration of the last ten time steps; the decision carries, besides the new pressure factor, what the per-step guard in the function object needs until the next decision (`guardRef`, the open `trial`) |
| `sidecar.log`, `report.txt` | as above |

## Where the model's answers are

The model's probabilities appear only in `decisions.jsonl` (`probs`), never in the solver log. The
solver log shows what was done (`increase_large: U 0.821 -> 0.901`); `decisions.jsonl` shows why: the
sentences the model read and the four probabilities the policy turned into that move. For iteration 100
of the steady run:

```
state.residuals.slowest_equation   "U: falling steadily, smooth, one to two orders of magnitude above its target"
probs                              diverging 0.04, safe_to_accelerate 0.62, stagnating 0.69, stuck_high 0.57
move                               increase_large   (diverging < 0.3 and stagnating > 0.6)
```

No API key, token or request header is written to any of these files; the sidecar reads the key from
the environment and logs only the model name, latency and token count.
