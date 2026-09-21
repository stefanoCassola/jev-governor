"""Transient (PIMPLE) mode: govern the non-final under-relaxation factors of the momentum equation and
the pressure field in the outer loop. The cost is the number of outer iterations per time step, subject
to every step meeting its outer-loop residual control.

Division of labour as in the steady mode: code computes features of the last time steps and states them
in words, the judge returns four yes/no probabilities, code maps them to one multiplicative move of
tau = a / (1 - a) of ONE factor at a time (coordinate search: in PIMPLE the pressure factor matters as
much as the momentum factor, and the rule 1 - alpha_U is far from optimal). Every increase is a trial
that is judged at the next decision and taken back if it did not pay off.

The per-step guard is not here: it runs in the function object after every time step, without the
sidecar, and reports what it did as `events` in the next state.

The design follows controller T of the study in study/ of this repository, with three changes that
answer its documented failures:
- a step that hits the iteration cap while its residuals fall monotonically is SLOW, not unstable;
  slow steps never lower the factors (that would slow the loop further);
- a trial that ended in an unstable step leaves a permanent cliff, which later trials approach by
  bisection in tau instead of stepping over it again; trials that merely did not pay off leave an
  expiring cap;
- cliffs are written to jevGovernor/memory.json and read again when a run is restarted from a
  checkpoint, so the restart does not repeat a fatal trial.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .policy import Decision

SETTLE = 2                        # time steps ignored after a factor change
MULT = {"increase_large": 2.0, "increase_small": 1.4, "increase_probe": 1.2, "hold": 1.0,
        "decrease_small": 1 / 1.4, "decrease_large": 0.5}
CAREFUL_ABOVE = 0.85              # momentum factor from which only small probes are made
CLIFF_STOP = 1.1                  # tau ratio to a known cliff below which a factor is not raised any more
OK, SLOW, UNSTABLE = 0, 1, 2


def _mean(x):
    return sum(x) / len(x) if x else float("nan")


def _tau(a: float) -> float:
    a = min(a, 0.999)
    return a / (1 - a)


def _from_tau(t: float) -> float:
    return t / (1 + t)


def tau_move(a: float, mult: float, lo: float, hi: float) -> float:
    return round(min(hi, max(lo, _from_tau(_tau(a) * mult))), 4)


class Step:
    __slots__ = ("idx", "n_outer", "cls", "rate", "rises", "slowest")

    def __init__(self, rec: dict, targets: dict, momentum: str, pressure: str):
        self.idx, self.n_outer, self.cls = rec["step"], rec["n_outer"], rec["class"]
        seqs = {momentum: rec.get("U") or [], pressure: rec.get("p") or []}
        level = {}
        for name, seq in seqs.items():
            tgt = targets.get(name) or 0
            if seq and seq[-1] and tgt:
                level[name] = seq[-1] / tgt
        self.slowest = max(level, key=level.get) if level else None
        seq = (seqs.get(self.slowest) or [])[:-1]          # the final outer iteration is unrelaxed
        self.rate, self.rises = None, 0.0
        if len(seq) >= 2 and all(v is not None and math.isfinite(v) and v > 0 for v in seq):
            self.rate = math.log10(seq[-1] / seq[0]) / (len(seq) - 1)
            self.rises = sum(1 for a, b in zip(seq, seq[1:]) if b > 1.05 * a) / (len(seq) - 1)


def features(steps: list[Step], n: int, last_change: int | None, interval: int, cap: int) -> dict:
    width = max(2 * interval, 20)
    start = n - width
    if last_change is not None:
        start = max(start, last_change + SETTLE)
    win = [s for s in steps if start < s.idx <= n]
    if len(win) < 3:
        win = steps[-3:]
    nout = [s.n_outer for s in win]
    rates = [s.rate for s in win if s.rate is not None]
    rises = [s.rises for s in win if s.rate is not None]
    slow: dict[str, int] = {}
    for s in win:
        if s.slowest:
            slow[s.slowest] = slow.get(s.slowest, 0) + 1
    half = len(nout) // 2
    drift = (_mean(nout[half:]) - _mean(nout[:half])) / max(_mean(nout), 1e-9) if half >= 2 else 0.0
    return {"window": [win[0].idx, win[-1].idx], "steps": len(win), "cap": cap,
            "n_mean": round(_mean(nout), 2), "n_max": max(nout),
            "slow_capped": sum(1 for s in win if s.cls == SLOW),
            "unstable": sum(1 for s in win if s.cls == UNSTABLE),
            "rate": round(_mean(rates), 4) if rates else None,
            "rises": round(_mean(rises), 3) if rises else 0.0,
            "slowest": max(slow, key=slow.get) if slow else None, "drift": round(drift, 3)}


def rate_word(r: float | None) -> str:
    if r is None:
        return "converges within one or two outer iterations"
    if r < -0.5:
        return "falls fast within a step (more than half a decade per outer iteration)"
    if r < -0.2:
        return "falls steadily within a step"
    if r < -0.05:
        return "falls slowly within a step"
    if r <= 0.02:
        return "hardly falls within a step"
    return "rises within a step"


def rises_word(x: float) -> str:
    return "monotonically" if x < 0.1 else ("with occasional rises" if x < 0.3 else "erratically, with frequent rises")


def verbal_state(feats: dict, *, description: str, f: dict, bounds: dict, active: str, last: dict | None,
                 best: tuple | None, limited: dict) -> dict:
    name = {"U": "momentum", "p": "pressure"}
    loop = (f"about {feats['n_mean']:.0f} outer iterations per time step over the last {feats['steps']} steps "
            f"(largest {feats['n_max']}, cap {feats['cap']}); ")
    if feats["slow_capped"] == 0 and feats["unstable"] == 0:
        loop += "every step met its residual control"
    else:
        parts = []
        if feats["slow_capped"]:
            parts.append(f"{feats['slow_capped']} of {feats['steps']} steps hit the cap while their residuals were "
                         "still falling steadily (slow, not unstable)")
        if feats["unstable"]:
            parts.append(f"{feats['unstable']} of {feats['steps']} steps were unstable (residuals rising or erratic "
                         "within the step, or the step failed)")
        loop += "; ".join(parts)
    within = (f"slowest equation {feats['slowest']}: its residual {rate_word(feats['rate'])}, "
              f"{rises_word(feats['rises'])}") if feats["slowest"] else "no residual information"
    obs = []
    if last is None:
        obs.append("no factor has been changed yet")
    else:
        obs.append(f"the {name[last['factor']]} factor was {last['direction']} "
                   f"{feats['window'][1] - last['step']} time steps ago (from {last['from']} to {last['to']}); "
                   "the steps right after the change are excluded")
        if last.get("n_before"):
            d = feats["n_mean"] / last["n_before"] - 1
            obs.append("since that change the outer iterations per step " +
                       (f"went down by {abs(d) * 100:.0f} %" if d < -0.05 else
                        f"went up by {d * 100:.0f} %" if d > 0.05 else "stayed about the same"))
        if last.get("by_guard"):
            obs.append("that change was made by the safety guard after a bad time step")
    if feats["drift"] > 0.1:
        obs.append("within the window the iterations per step are creeping up")
    elif feats["drift"] < -0.1:
        obs.append("within the window the iterations per step are coming down")
    if best is not None and feats["n_mean"] > 1.15 * best[0]:
        obs.append(f"earlier in this run about {best[0]:.0f} outer iterations per step were reached with "
                   f"factors {best[1]}")
    for k in ("U", "p"):
        if f[k] >= bounds[k][1] - 1e-6:
            obs.append(f"the {name[k]} factor is at its upper limit")
        elif limited.get(k):
            obs.append(f"the {name[k]} factor cannot be raised further for now: {limited[k]}")
        if f[k] <= bounds[k][0] + 1e-6:
            obs.append(f"the {name[k]} factor is at its lower limit")
    solver = description or ("Transient incompressible CFD, PIMPLE algorithm with outer correctors and a large "
                             "time step.")
    solver += (" Decided are the under-relaxation factors of the non-final outer iterations; fewer outer "
               "iterations per time step is better, provided every step meets its residual control.")
    return {"solver": solver, "momentum_factor": f["U"], "pressure_factor": f["p"],
            "factor_to_change_next": name[active], "outer_loop": loop, "within_step_convergence": within,
            "observations": obs}


Q_TEXT_T = {
    "diverging": (
        "Do `outer_loop`, `within_step_convergence` and `observations` show that the outer loop is failing "
        "or becoming unstable: unstable time steps, residuals rising or behaving erratically within a step, "
        "or a sharp increase of the iterations per step right after a factor was raised? Steps that hit the "
        "cap while their residuals fall steadily are slow, not unstable.",
        {"true": "The outer loop is failing or becoming unstable.",
         "false": "The outer loop converges in every step, quickly or slowly, or is merely slow."}),
    "safe_to_accelerate": (
        "Do the outer loops behave smoothly in every time step (residuals falling without erratic rises, no "
        "unstable steps), so that raising the factor named in `factor_to_change_next` (less under-relaxation) "
        "would be safe to try?",
        {"true": "Smooth behaviour in every step; safe to relax less.",
         "false": "Not safe: unstable steps or residuals behaving erratically."}),
    "stagnating": (
        "Does each time step need many outer iterations because the residuals fall only slowly within a "
        "step, as opposed to steps that already converge in a handful of outer iterations?",
        {"true": "Many outer iterations per step, slow convergence within a step.",
         "false": "Steps already converge in a handful of outer iterations."}),
    "stuck_high": (
        "According to `observations`, did the most recent increase of a factor make things worse or bring no "
        "benefit (more or the same outer iterations per step than before it), so that it should be taken back?",
        {"true": "The last increase did not pay off.",
         "false": "The last change paid off, or the last change was not an increase."}),
}


class RulesJudgeT:
    """Thresholds on the same features; the baseline that any claim for the model has to beat."""
    name = "rules"

    def judge(self, feats: dict, state: dict) -> tuple[dict, dict]:
        ratio = feats.get("trial_ratio")
        erratic = feats["rises"] >= 0.3
        if feats["unstable"] >= max(2, feats["steps"] // 4) or (ratio is not None and ratio > 1.5):
            div = 1.0
        else:
            div = 0.7 if (feats["unstable"] > 0 or erratic) else 0.0
        return {"diverging": div,
                "safe_to_accelerate": 1.0 if (feats["unstable"] == 0 and not erratic) else 0.0,
                "stagnating": 1.0 if feats["n_mean"] >= 8 else 0.0,
                "stuck_high": 1.0 if (ratio is not None and ratio >= 0.97) else 0.0}, {}


class GovernorT:
    """One instance per run; feed it every state file in order."""

    def __init__(self, judge, memory: Path | None = None):
        self.judge = judge
        self.memory = memory
        self.steps: list[Step] = []
        self.ingested_to = 0
        self.events_seen: set[int] = set()
        self.cooldown = 0
        self.last: dict | None = None                     # last change, for the verbal state
        self.cap: dict = {"U": None, "p": None}           # expiring: an increase that did not pay off
        self.cap_set_at = {"U": 0, "p": 0}
        self.fails = {"U": 0, "p": 0}
        self.cliffs: dict = {"U": [], "p": []}            # permanent: [value that was unstable, other factor]
        self.best: tuple | None = None
        self.active = "U"
        self.trial: dict | None = None
        self.commanded: dict | None = None
        self.in_flight = 0
        self.memory_loaded = False

    # ---------------------------------------------------------------- memory

    def _load_memory(self, first_step: int, interval: int) -> None:
        self.memory_loaded = True
        if self.memory is None or not self.memory.exists() or first_step <= interval:
            return                                         # a fresh run starts without the cliffs of an older one
        try:
            data = json.loads(self.memory.read_text())
            for k in ("U", "p"):
                self.cliffs[k] = [list(c) for c in data.get("cliffs", {}).get(k, [])]
        except (OSError, ValueError):
            pass

    def _save_memory(self) -> None:
        if self.memory is not None:
            tmp = self.memory.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cliffs": self.cliffs}))
            tmp.rename(self.memory)

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _other(k: str) -> str:
        return "p" if k == "U" else "U"

    def _cliff(self, k: str, f: dict) -> float | None:
        """Lowest known cliff of factor k that applies with the other factor where it is now."""
        other = f[self._other(k)]
        vals = [v for v, o in self.cliffs[k] if other >= o - 0.02]
        return min(vals) if vals else None

    def _raise(self, k: str, f: dict, move: str, bounds: dict) -> tuple[float, str | None]:
        """New value of factor k for an increase, and why it is limited (if it is)."""
        hi = bounds[k][1] if self.cap[k] is None else min(bounds[k][1], self.cap[k])
        new = tau_move(f[k], MULT[move], bounds[k][0], hi)
        why = None
        if self.cap[k] is not None and f[k] >= self.cap[k] - 1e-6:
            why = "a recent increase did not pay off"
        cliff = self._cliff(k, f)
        if cliff is not None:
            room = _tau(cliff) / _tau(f[k])
            if room < CLIFF_STOP:
                return f[k], "it is just below a value at which the run became unstable"
            half = round(_from_tau(_tau(f[k]) * math.sqrt(room)), 4)    # bisection in tau towards the cliff
            new = min(new, half)
        return new, why

    def observe(self, state: dict) -> None:
        cfg = state["config"]
        if not self.memory_loaded and state.get("steps"):
            self._load_memory(state["steps"][0]["step"], cfg["interval"])
        for rec in state.get("steps", []):
            if rec["step"] > self.ingested_to:
                self.steps.append(Step(rec, state["targets"], cfg["momentum"], cfg["pressure"]))
                self.ingested_to = rec["step"]
        for ev in state.get("events", []):
            if ev["step"] in self.events_seen:
                continue
            self.events_seen.add(ev["step"])
            self._guard_event(ev, cfg)

    def _guard_event(self, ev: dict, cfg: dict) -> None:
        """The function object's guard acted: record what it means for the search."""
        key = {cfg["momentum"]: "U", cfg["pressure"]: "p"}
        self.trial, self.commanded, self.in_flight = None, None, 0
        self.cooldown = 2
        if ev["action"] == "take_back":
            k = key.get(ev["factor"], "U")
            tried, other = ev["from"][k], ev["from"][self._other(k)]
            if ev.get("cliff"):
                self.cliffs[k].append([tried, other])
                self._save_memory()
            else:
                self.cap[k], self.cap_set_at[k] = ev["to"][k], ev["step"]
            self.fails[k] += 1
            self.active = self._other(k)
            self.last = {"step": ev["step"], "factor": k, "direction": "decreased", "from": tried,
                         "to": ev["to"][k], "n_before": None, "by_guard": True}
        else:                                              # lower_both: unstable without a recent trial
            for k in ("U", "p"):
                self.cap[k], self.cap_set_at[k] = ev["to"][k], ev["step"]
                self.fails[k] += 1
            self.last = {"step": ev["step"], "factor": "U", "direction": "decreased", "from": ev["from"]["U"],
                         "to": ev["to"]["U"], "n_before": None, "by_guard": True}

    # ---------------------------------------------------------------- decide

    def decide(self, state: dict) -> Decision:
        cfg = state["config"]
        n, N = state["iteration"], cfg["interval"]
        momentum, pressure = cfg["momentum"], cfg["pressure"]
        bounds = {"U": tuple(cfg["bounds"]), "p": tuple(cfg["pressure_bounds"])}
        ctl = state["controls"]
        self.observe(state)

        u = ctl["equations"].get(momentum)
        p = ctl["fields"].get(pressure)
        f = {"U": 1.0 if u is None else float(u), "p": 1.0 if p is None else float(p)}

        def decision(move, nf, note, info=None):
            eq = {momentum: nf["U"]} if nf["U"] != f["U"] else {}
            fl = {pressure: nf["p"]} if nf["p"] != f["p"] else {}
            guard = {"ref": round(self.trial["n_before"] if self.trial else self._n_ref, 2)}
            if self.trial:
                guard["trial"] = {"factor": momentum if self.trial["factor"] == "U" else pressure,
                                  "from": self.trial["from"], "id": self.trial["id"]}
            return Decision(n, move, eq, fl, False, False, note, info or {}, guard)

        self._n_ref = _mean([s.n_outer for s in self.steps[-N:]]) if self.steps else -1.0
        if len(self.steps) < 3:
            return decision("hold", f, "hold (collecting time steps)")

        # async: only one change in flight; the guard's events have already cleared `commanded`
        if self.commanded is not None:
            if all(abs(f[k] - self.commanded[k]) < 1e-3 for k in ("U", "p")):
                self.commanded, self.in_flight = None, 0
                applied = ctl.get("last_change_applied_at")
                if self.last is not None and applied is not None:
                    self.last["step"] = applied
                    if self.trial is not None:
                        self.trial["step"] = applied
            elif self.in_flight < 8:
                self.in_flight += 1
                return decision("hold", f, "hold (the last change is not applied yet)")
            else:
                self.commanded, self.in_flight = None, 0

        feats = features(self.steps, n, self.last["step"] if self.last else None, N, state["cap"])
        self._n_ref = feats["n_mean"]
        clean = feats["slow_capped"] == 0 and feats["unstable"] == 0
        if clean and (self.best is None or feats["n_mean"] < self.best[0]):
            self.best = (feats["n_mean"], f"{f['U']} (momentum) / {f['p']} (pressure)")
        feats["trial_ratio"] = (feats["n_mean"] / self.trial["n_before"]
                                if self.trial and self.trial.get("n_before") else None)

        ttl = max(10 * N, 100)
        for k in ("U", "p"):                               # expiring caps, with exponential back-off
            if self.cap[k] is not None and n - self.cap_set_at[k] >= ttl * 2 ** max(self.fails[k] - 1, 0):
                self.cap[k] = None

        limited = {}
        for k in ("U", "p"):
            _, why = self._raise(k, f, "increase_probe", bounds)
            if why:
                limited[k] = why
        if (limited.get(self.active) or f[self.active] >= bounds[self.active][1] - 1e-6) and \
                not limited.get(self._other(self.active)) and \
                f[self._other(self.active)] < bounds[self._other(self.active)][1] - 1e-6:
            self.active = self._other(self.active)

        verbal = verbal_state(feats, description=cfg.get("description", ""), f=f, bounds=bounds,
                              active=self.active, last=self.last, best=self.best, limited=limited)
        if clean and feats["n_mean"] <= cfg.get("floor", 3.0):
            probs, info = dict.fromkeys(Q_TEXT_T, 0.0), {"floor": True}
        else:
            probs, info = self.judge.judge(feats, verbal)

        nf = dict(f)
        k = self.active
        if probs["diverging"] > 0.6:
            k = self.trial["factor"] if self.trial is not None else self.active
            move = "decrease_large" if probs["diverging"] > 0.8 else "decrease_small"
            nf[k] = tau_move(f[k], MULT[move], *bounds[k])
            if self.trial is not None:
                nf[k] = min(self.trial["from"], nf[k])
                self.cap[k], self.cap_set_at[k] = nf[k], n
                self.fails[k] += 1
            self.cooldown, self.trial = 2, None
        elif self.cooldown > 0:
            move = "hold"
            self.cooldown -= 1
        elif self.trial is not None and probs["stuck_high"] > 0.6:
            k = self.trial["factor"]
            move = "take_back"                             # the last increase did not pay off
            nf[k] = self.trial["from"]
            self.cap[k], self.cap_set_at[k] = nf[k], n
            self.fails[k] += 1
            self.trial = None
            self.active = self._other(k)
        else:
            self.trial = None                              # a previous increase, if any, is accepted
            if probs["diverging"] < 0.3 and probs["stagnating"] > 0.6:
                move = "increase_large"
            elif probs["safe_to_accelerate"] > 0.6:
                move = "increase_small"
            else:
                move = "hold"
            if move != "hold":                             # approach the stability limit in small steps
                careful = k == "p" or f[k] >= CAREFUL_ABOVE or self.fails[k] > 0 or bool(self.cliffs[k])
                if careful:
                    move = "increase_probe" if (k == "p" or f[k] >= CAREFUL_ABOVE) else "increase_small"
                nf[k], _ = self._raise(k, f, move, bounds)
                if nf[k] <= f[k]:
                    nf[k], move = f[k], "hold"
            if move == "hold":
                self.active = self._other(self.active)

        if nf != f:
            up = nf[k] > f[k]
            self.last = {"step": n, "factor": k, "direction": "increased" if up else "decreased",
                         "from": f[k], "to": nf[k], "n_before": feats["n_mean"]}
            if up:
                self.trial = {"factor": k, "from": f[k], "to": nf[k], "id": n, "step": n,
                              "n_before": feats["n_mean"]}
            self.commanded = dict(nf)

        name = {"U": momentum, "p": pressure}
        note = f"{move}: {name[k]} {f[k]:.3f} -> {nf[k]:.3f}" if nf != f else f"{move} ({momentum} {f['U']:.3f}, " \
                                                                             f"{pressure} {f['p']:.3f})"
        if info.get("floor"):
            note = f"hold: at the floor of {feats['n_mean']:.1f} outer iterations per step"
        info.update(probs=probs, state=verbal, judge=self.judge.name, features=feats, factors=f, active=self.active,
                    guard_events=state.get("events", []),
                    cap=dict(self.cap), cliffs={a: list(b) for a, b in self.cliffs.items()})
        return decision(move, nf, note, info)
