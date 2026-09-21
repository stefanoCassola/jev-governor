"""Controller T: runtime control of the PIMPLE outer-loop under-relaxation in transient runs.

Same division of labour as the steady controller (ctl3.py): code computes features of the last time steps
and states them in words, the judge (Jev or the rule-based twin) returns four yes/no probabilities, code maps
them to a multiplicative move of tau = a / (1 - a) of ONE factor at a time (coordinate search over the momentum
and the pressure factor, because in PIMPLE both matter), and enforces bounds, a cooldown, expiring caps and a
per-step guard. What is controlled are the non-final factors; the final outer
iteration of every step is unrelaxed (factor 1). The cost to minimise is the number of outer iterations per
time step, subject to every step meeting its outer-loop residual control.
"""
from __future__ import annotations

import math
import sys
import time

SETTLE = 2            # time steps ignored after a factor change
U_MULT = {"increase_large": 2.0, "increase_small": 1.4, "hold": 1.0, "decrease_small": 1 / 1.4,
          "decrease_large": 0.5}


def _mean(x):
    return sum(x) / len(x) if x else float("nan")


def step_rate(step, targets: dict) -> tuple[str | None, float | None, float]:
    """Slowest field of one step (largest final residual/target), its mean decades per outer iteration
    (negative = falling) and the fraction of outer iterations at which its residual rose."""
    worst, wr = None, -1e9
    for f, tgt in targets.items():
        seq = [o.get(f) for o in step.outer if o.get(f)]
        if len(seq) >= 1 and tgt:
            r = math.log10(seq[-1] / tgt)
            if r > wr:
                worst, wr = f, r
    if worst is None:
        return None, None, 0.0
    seq = [o.get(worst) for o in step.outer if o.get(worst)]
    if len(seq) < 2 or any((not math.isfinite(v)) or v <= 0 for v in seq):
        return worst, None, 0.0
    rate = math.log10(seq[-1] / seq[0]) / (len(seq) - 1)
    rises = sum(1 for a, b in zip(seq, seq[1:]) if b > a * 1.05) / (len(seq) - 1)
    return worst, rate, rises


def features(steps, prof: dict, n: int, last_change: int | None, N: int) -> dict:
    W = max(2 * N, 20)
    start = n - W
    if last_change is not None:
        start = max(start, last_change + SETTLE)
    win = [s for s in steps if start < s.idx <= n]
    if len(win) < 3:
        win = steps[-3:]
    cap = prof["cap"]
    nout = [s.n_outer for s in win]
    rates, rises, slow = [], [], {}
    for s in win:
        f, r, ri = step_rate(s, prof["targets"])
        if f is not None:
            slow[f] = slow.get(f, 0) + 1
        if r is not None:
            rates.append(r); rises.append(ri)
    half = len(nout) // 2
    drift = (_mean(nout[half:]) - _mean(nout[:half])) / max(_mean(nout), 1e-9) if half >= 2 else 0.0
    bad = any(any((not math.isfinite(v)) for v in o.values()) for s in win for o in s.outer)
    return {"window": [win[0].idx, win[-1].idx], "n_mean": round(_mean(nout), 2), "n_max": max(nout),
            "unconverged": sum(1 for s in win if not s.converged), "steps": len(win), "cap": cap,
            "rate": round(_mean(rates), 4) if rates else None, "rises": round(_mean(rises), 3) if rises else 0.0,
            "slowest": max(slow, key=slow.get) if slow else None, "drift": round(drift, 3), "bad": bad,
            "co_max": win[-1].co_max}


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


def verbal_state(feats: dict, prof: dict, f: dict, hist: list, b: dict, active: str) -> dict:
    loop = (f"about {feats['n_mean']:.0f} outer iterations per time step over the last {feats['steps']} steps "
            f"(largest {feats['n_max']}, cap {feats['cap']}); ")
    loop += ("every step met its residual control" if feats["unconverged"] == 0 else
             f"{feats['unconverged']} of {feats['steps']} steps hit the cap without meeting the residual control")
    within = (f"slowest equation {feats['slowest']}: its residual {rate_word(feats['rate'])}, "
              f"{rises_word(feats['rises'])}") if feats["slowest"] else "no residual information"
    name = {"U": "momentum", "p": "pressure"}
    obs = []
    last = next((h for h in reversed(hist) if h["changed"]), None)
    if last is None:
        obs.append("no factor has been changed yet")
    else:
        obs.append(f"the {name[last['factor']]} factor was {last['direction']} {feats['window'][1] - last['iteration']} time "
                   f"steps ago (from {last['from']} to {last['to']}); the steps right after the change are excluded")
        if last.get("n_before"):
            d = feats["n_mean"] / last["n_before"] - 1
            obs.append("since that change the outer iterations per step " +
                       (f"went down by {abs(d) * 100:.0f} %" if d < -0.05 else
                        f"went up by {d * 100:.0f} %" if d > 0.05 else "stayed about the same"))
    if feats["drift"] > 0.1:
        obs.append("within the window the iterations per step are creeping up")
    elif feats["drift"] < -0.1:
        obs.append("within the window the iterations per step are coming down")
    if feats.get("best_n") is not None and feats["n_mean"] > 1.15 * feats["best_n"]:
        obs.append(f"earlier in this run about {feats['best_n']:.0f} outer iterations per step were reached with "
                   f"factors {feats['best_f']}")
    for k in ("U", "p"):
        if f[k] >= b[k][1] - 1e-6:
            obs.append(f"the {name[k]} factor is at its upper limit")
        if f[k] <= b[k][0] + 1e-6:
            obs.append(f"the {name[k]} factor is at its lower limit")
    return {"solver": prof["context"], "momentum_factor": f["U"], "pressure_factor": f["p"],
            "factor_to_change_next": name[active], "outer_loop": loop, "within_step_convergence": within,
            "observations": obs}


Q_TEXT = {
    "diverging": ("Do `outer_loop`, `within_step_convergence` and `observations` show that the outer loop is "
                  "failing or becoming unstable: time steps hitting the iteration cap, residuals rising or "
                  "behaving erratically within a step, or a sharp increase of the iterations per step right after "
                  "a factor was raised?",
                  {"true": "The outer loop is failing or becoming unstable.",
                   "false": "The outer loop converges in every step, quickly or slowly."}),
    "safe_to_accelerate": ("Do the outer loops converge smoothly in every time step, so that raising the factor named in "
                           "`factor_to_change_next` (less under-relaxation) would be safe to try?",
                           {"true": "Smooth convergence in every step; safe to relax less.",
                            "false": "Not safe: steps hit the cap or residuals behave erratically."}),
    "stagnating": ("Does each time step need many outer iterations because the residuals fall only slowly within "
                   "a step, as opposed to steps that already converge in a handful of outer iterations?",
                   {"true": "Many outer iterations per step, slow convergence within a step.",
                    "false": "Steps already converge in a handful of outer iterations."}),
    "stuck_high": ("According to `observations`, did the most recent increase of a factor make things worse or "
                   "bring no benefit (more or the same outer iterations per step than before it), so that it "
                   "should be taken back?",
                   {"true": "The last increase did not pay off.",
                    "false": "The last change paid off, or the last change was not an increase."}),
}


class JevT:
    name = "jevT"

    def __init__(self, model: str):
        from typesafe_sdk import Noul, TypeSafeClient
        self.client = TypeSafeClient(model=model)
        self.q = {k: Noul(instructions=t, criteria=c) for k, (t, c) in Q_TEXT.items()}

    def judge(self, feats, prof, f, hist, b, active) -> tuple[dict, dict]:
        state = verbal_state(feats, prof, f, hist, b, active)
        t0 = time.time()
        r, attempt, outage = None, 0, 0.0
        while r is None:
            try:
                r = self.client.system_one(state=state, questions=self.q)
            except Exception as ex:  # noqa: BLE001  never skip a decision
                attempt += 1
                w = min(300, 2 * attempt)
                print(f"API error (attempt {attempt}): {ex!r}"[:300], file=sys.stderr, flush=True)
                time.sleep(w)
                outage += w
        p = {k: r.answers[k].noul for k in self.q}
        info = {"latency_s": round(time.time() - t0 - outage, 3), "state": state, "probs": p,
                "model": getattr(r, "model", None)}
        if attempt:
            info.update(api_retries=attempt, api_outage_s=outage)
        return p, info


class HeurT:
    """Rule-based twin: same features, mapping, cap, cooldown and guard; thresholds instead of the model."""
    name = "heurT"

    def judge(self, feats, prof, f, hist, b, active) -> tuple[dict, dict]:
        last = next((h for h in reversed(hist) if h["changed"]), None)
        up = last is not None and last["direction"] == "increased" and last.get("n_before")
        ratio = feats["n_mean"] / last["n_before"] if up else None
        erratic = feats["rises"] >= 0.3
        div = 1.0 if (feats["unconverged"] >= max(2, feats["steps"] // 4) or (ratio is not None and ratio > 1.5)) else (
            0.7 if (feats["unconverged"] > 0 or erratic) else 0.0)
        safe = 1.0 if (feats["unconverged"] == 0 and not erratic) else 0.0
        stag = 1.0 if feats["n_mean"] >= 8 else 0.0
        stuck = 1.0 if (ratio is not None and ratio >= 0.97) else 0.0
        return {"diverging": div, "safe_to_accelerate": safe, "stagnating": stag, "stuck_high": stuck}, {}


def _tau_move(v: float, mult: float, lo: float, hi: float) -> float:
    t = v / (1 - v) * mult
    return round(min(hi, max(lo, t / (1 + t))), 4)


class ControllerT:
    """Two-factor coordinate search. One factor is 'active' at a time; increases are trials that are judged at
    the next decision and taken back (with an expiring cap on that factor) if they did not pay off."""

    def __init__(self, judge, prof: dict, b: dict, N: int):
        self.judge, self.prof, self.b, self.N = judge, prof, b, N
        self.ttl = max(10 * N, 100)
        self.cooldown = 0
        self.last_change: int | None = None
        self.cap: dict = {"U": None, "p": None}
        self.cap_set_at: dict = {"U": 0, "p": 0}
        self.fails: dict = {"U": 0, "p": 0}     # increases of this factor that had to be taken back
        self.best_n: float | None = None
        self.best_f: str | None = None
        self.active = "U"
        self.trial: dict | None = None      # last increase that has not been judged yet
        self.guard_at = -10 ** 9
        self.n_ref: float | None = None     # iterations per step at the last decision

    def _hi(self, k: str) -> float:
        return self.b[k][1] if self.cap[k] is None else min(self.b[k][1], self.cap[k])

    def _set_cap(self, k: str, v: float, n: int) -> None:
        self.cap[k], self.cap_set_at[k] = v, n
        self.fails[k] += 1

    def _other(self, k: str) -> str:
        return "p" if k == "U" else "U"

    def step_guard(self, steps, n: int, f: dict, hist: list):
        """Code-only check after EVERY time step: a step that hits the cap (or a non-finite residual) right
        after an increase takes that increase back at once; otherwise both factors are lowered."""
        s = steps[-1]
        hard = (not s.converged) or any((not math.isfinite(v)) for o in s.outer for v in o.values())
        ref = self.trial["n_before"] if self.trial is not None else self.n_ref
        creep = ref is not None and s.n_outer >= max(1.5 * ref, ref + 6)   # iterations creep up before a blow-up
        bad = hard or creep
        if not bad or n - self.guard_at < max(2, self.N // 2):
            return None
        self.guard_at = n
        nf = dict(f)
        if self.trial is not None and n - self.trial["iteration"] <= 2 * self.N:
            k = self.trial["factor"]
            nf[k] = self.trial["from"]
            self._set_cap(k, nf[k], n)
            reason = f"outer iterations rose sharply or hit the cap after the {k} factor was raised: increase taken back"
            self.active = self._other(k)
        else:
            nf["U"] = _tau_move(f["U"], U_MULT["decrease_small"], *self.b["U"])
            nf["p"] = _tau_move(f["p"], U_MULT["decrease_small"], *self.b["p"])
            reason = "outer iterations rose sharply or hit the cap: both factors lowered"
        self.trial = None
        self.cooldown = 2
        self.last_change = n
        k = "U" if nf["U"] != f["U"] else "p"
        rec = {"iteration": n, "changed": nf != f, "n_before": None, "factor": k, "from": f[k], "to": nf[k],
               "direction": "decreased"}
        return nf, {"info": {"guard": reason, "move": "guard"}, "hist": rec}

    def prepare(self, steps, n: int, f: dict) -> dict:
        """Fast part of a decision: features and bookkeeping (no model call)."""
        feats = features(steps, self.prof, n, self.last_change, self.N)
        if feats["unconverged"] == 0 and (self.best_n is None or feats["n_mean"] < self.best_n):
            self.best_n, self.best_f = feats["n_mean"], f"{f['U']} (momentum) / {f['p']} (pressure)"
        feats["best_n"], feats["best_f"] = self.best_n, self.best_f
        self.n_ref = feats["n_mean"]
        for k in ("U", "p"):
            if self.cap[k] is not None and n - self.cap_set_at[k] >= self.ttl * 2 ** (self.fails[k] - 1):   # back-off
                self.cap[k] = None
        if f[self.active] >= self._hi(self.active) - 1e-6 and f[self._other(self.active)] < self._hi(self._other(self.active)) - 1e-6:
            self.active = self._other(self.active)
        feats["floor"] = feats["n_mean"] <= self.prof.get("floor", 3.0) and feats["unconverged"] == 0
        return feats

    def judge_call(self, feats: dict, f: dict, hist: list) -> tuple[dict, dict]:
        """Slow part: the judgments (model call or thresholds). Touches no controller state."""
        if feats["floor"]:
            return {"diverging": 0.0, "safe_to_accelerate": 0.0, "stagnating": 0.0, "stuck_high": 0.0}, {"floor": True}
        return self.judge.judge(feats, self.prof, dict(f), list(hist), self.b, self.active)

    def decide(self, steps, n: int, f: dict, hist: list) -> tuple[dict, dict]:
        feats = self.prepare(steps, n, f)
        probs, info = self.judge_call(feats, f, hist)
        return self.finish(feats, probs, info, n, f, hist)

    def finish(self, feats: dict, probs: dict, info: dict, n: int, f: dict, hist: list) -> tuple[dict, dict]:
        """Fast part: policy. n is the time step at which the new factors are handed to the solver."""
        nf = dict(f)
        k = self.active
        if probs["diverging"] > 0.6:
            k = self.trial["factor"] if self.trial is not None else self.active
            move = "decrease_large" if probs["diverging"] > 0.8 else "decrease_small"
            if self.trial is not None:
                nf[k] = min(self.trial["from"], _tau_move(f[k], U_MULT[move], *self.b[k]))
                self._set_cap(k, nf[k], n)
            else:
                nf[k] = _tau_move(f[k], U_MULT[move], *self.b[k])
            self.cooldown = 2
            self.trial = None
        elif self.cooldown > 0:
            move = "hold"
            self.cooldown -= 1
        elif self.trial is not None and probs["stuck_high"] > 0.6:
            k = self.trial["factor"]
            move = "take_back"                       # the last increase did not pay off
            nf[k] = self.trial["from"]
            self._set_cap(k, nf[k], n)
            self.trial = None
            self.active = self._other(k)
        else:
            self.trial = None                        # a previous increase, if any, is accepted
            if probs["diverging"] < 0.3 and probs["stagnating"] > 0.6:
                move = "increase_large"
            elif probs["safe_to_accelerate"] > 0.6:
                move = "increase_small"
            else:
                move = "hold"
            if move == "increase_large" and (self.fails[k] > 0 or k == "p" or f[k] >= 0.85):
                move = "increase_small"              # approach the stability limit in small steps
            if move != "hold":
                nf[k] = _tau_move(f[k], U_MULT[move], self.b[k][0], self._hi(k))
                if nf[k] == f[k]:
                    move = "hold"
            if move == "hold":
                self.active = self._other(self.active)
        changed = nf != f
        if changed:
            self.last_change = n
            if nf[k] > f[k]:
                self.trial = {"factor": k, "from": f[k], "iteration": n, "n_before": feats["n_mean"]}
        rec_hist = {"iteration": n, "changed": changed, "n_before": feats["n_mean"], "factor": k, "from": f[k], "to": nf[k],
                    "direction": "increased" if nf[k] > f[k] else "decreased"}
        info.update(move=move, factor=k, probs=probs, features=feats, cap=dict(self.cap), active=self.active)
        nf["turb"] = f.get("turb")
        return nf, {"info": info, "hist": rec_hist}
