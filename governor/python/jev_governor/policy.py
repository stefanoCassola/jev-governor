"""The policy: code owns the control law, the judge only supplies four probabilities.

Moves are multiplicative in tau = a / (1 - a) (the pseudo-time step equivalent of an
under-relaxation factor a), so a step means the same near 0.3 as near 0.95. Hard guards,
a cooldown after every decrease and a stall ceiling with a time-to-live live here, in code,
and do not depend on the judge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import features as ft

MOVES = {"increase_large": 2.0, "increase_small": 1.4, "hold": 1.0,
         "decrease_small": 1 / 1.4, "decrease_large": 0.5}
P_BOUNDS = (0.05, 0.95)


@dataclass
class Decision:
    iteration: int
    move: str
    equations: dict[str, float]
    fields: dict[str, float]
    upwind: bool
    stop: bool
    note: str
    info: dict = field(default_factory=dict)
    guard: dict | None = None        # PIMPLE mode: what the function object's per-step guard needs

    @property
    def changed(self) -> bool:
        return bool(self.equations or self.fields)

    def to_foam(self) -> str:
        """OpenFOAM dictionary read by the function object."""
        out = [f"iteration       {self.iteration};"]
        if self.changed:
            out.append("relaxationFactors\n{")
            for group, vals in (("equations", self.equations), ("fields", self.fields)):
                if vals:
                    out.append(f"    {group}\n    {{")
                    out += [f"        {k:<15} {v:.4f};" for k, v in vals.items()]
                    out.append("    }")
            out.append("}")
        if self.guard is not None:
            out.append(f"guardRef        {self.guard['ref']};")
            if "trial" in self.guard:
                t = self.guard["trial"]
                out.append(f"trial\n{{\n    factor          {t['factor']};\n    from            {t['from']:.4f};\n"
                           f"    id              {t['id']};\n}}")
        out.append(f"upwind          {'true' if self.upwind else 'false'};")
        out.append(f"stop            {'true' if self.stop else 'false'};")
        out.append('note            "' + " ".join(self.note.replace('"', "'").replace("\\", "/").split()) + '";')
        return "\n".join(out) + "\n"


def _step(a: float, mult: float, lo: float, hi: float) -> float:
    a = min(a, 0.999)
    tau = a / (1 - a) * mult
    return round(min(hi, max(lo, tau / (1 + tau))), 4)


class Governor:
    """One instance per run; feed it every state file in order."""

    def __init__(self, judge, *, ceiling_ttl: int | None = 300, upwind_restore_after: int = 4,
                 stop_after_floor_divergences: int = 0, settle_after: int | None = None):
        """settle_after: iterations without a new residual low after which the governor returns to the
        best factor it has seen and freezes (default: max(12 intervals, 300)). Some cases plateau above
        their residualControl targets whatever the factors are; changing factors on a plateau only
        re-excites transients. A frozen governor still reacts to its hard guard."""
        self.judge = judge
        self.ceiling_ttl = ceiling_ttl
        self.upwind_restore_after = upwind_restore_after
        self.stop_after = stop_after_floor_divergences
        self.samples: list[ft.Sample] = []
        self.cooldown = 0
        self.last_change: dict | None = None
        self.last_res: dict | None = None
        self.best_level: float | None = None
        self.best_it = 0
        self.stall_from = 0                      # the stall clock restarts after a back-off
        self.ceiling: float | None = None
        self.ceiling_set_at = 0
        self.calm_with_upwind = 0
        self.upwind_sticky = False
        self.upwind_restored_at: int | None = None
        self.floor_divergences = 0
        self.settle_after = settle_after
        self.best_u: float | None = None
        self.frozen = False
        self.commanded: float | None = None      # momentum factor of the last change that was sent
        self.in_flight = 0                       # decisions spent waiting for that change to show up
        self.ingested_to = 0

    # ------------------------------------------------------------------ helpers

    def observe(self, state: dict) -> None:
        """Take the residual samples of a state without deciding (async mode: a newer state exists)."""
        names = state["fields"]
        for it, vals in state["samples"]:
            if it > self.ingested_to:
                self.samples.append(ft.Sample(it, dict(zip(names, vals))))
                self.ingested_to = it

    def _guard(self, now: dict) -> str | None:
        for k, v in now.items():
            if v is None or not math.isfinite(v):
                return f"non-finite {k} residual"
        if self.last_res:
            for k, v in now.items():
                old = self.last_res.get(k)
                if old and old > 0 and v > 10 * old and v > 1e-3:
                    return f"{k} residual rose more than tenfold since the last decision"
        return None

    # ------------------------------------------------------------------- decide

    def decide(self, state: dict) -> Decision:
        cfg = state["config"]
        n = state["iteration"]
        interval = cfg["interval"]
        lo, hi = cfg["bounds"]
        momentum, pressure = cfg["momentum"], cfg["pressure"]
        ctl = state["controls"]
        upwind_now = bool(ctl["upwind"])

        seen = min(getattr(self, "_scored", 0), len(self.samples))
        self.observe(state)
        self._scored = len(self.samples)
        targets = {k: state["targets"].get(k, 0.0) for k in state["fields"]}

        u = ctl["equations"].get(momentum)
        u = hi if u is None else float(u)       # not relaxed at all == factor 1: start from the top

        if len(self.samples) < 5:
            return Decision(n, "hold", {}, {}, upwind_now, False, "hold (collecting residual history)")

        feats = ft.compute(self.samples, targets, n,
                           self.last_change["iteration"] if self.last_change else None, interval)
        for s in self.samples[seen:]:
            lv = ft.worst_level(s, targets)
            if lv is not None and (self.best_level is None or lv < self.best_level - 0.1):
                self.best_level, self.best_it, self.best_u, self.stall_from = lv, s.it, u, s.it
        feats.no_progress_iters = n - self.best_it

        if self.ceiling is not None and self.ceiling_ttl is not None and n - self.ceiling_set_at >= self.ceiling_ttl:
            self.ceiling = None                  # stability need not be monotonic in the factor: probe again

        now = self.samples[-1].res
        guard = self._guard(now)
        self.last_res = {k: v for k, v in now.items() if v is not None and math.isfinite(v)}

        # Async mode: a state can be older than the last change. Deciding on it would repeat the same
        # move from a stale factor, so only one change is in flight at a time. The guard is not held back.
        if self.commanded is not None:
            if abs(u - self.commanded) < 1e-3:
                self.commanded, self.in_flight = None, 0
                if self.last_change:
                    # the function object reports the iteration of the re-read (exact); older ones do not
                    applied = ctl.get("last_change_applied_at")
                    self.last_change["iteration"] = applied if applied is not None else n - interval
            elif guard:
                u = min(u, self.commanded)                        # whichever is active, go below both
                self.commanded, self.in_flight = None, 0
            elif self.in_flight < 8:
                self.in_flight += 1
                return Decision(n, "hold", {}, {}, upwind_now, False,
                                f"hold (the change to {momentum} = {self.commanded:.3f} is not applied yet)")
            else:
                self.commanded, self.in_flight = None, 0          # lost: carry on from what the solver reports

        verbal = ft.verbal_state(feats, description=cfg.get("description", ""), momentum=momentum, factor=u,
                                 bounds=(lo, hi), last_change=self.last_change, upwind=upwind_now)
        if guard:
            probs, info = {"diverging": 1.0, "safe_to_accelerate": 0.0, "stagnating": 0.0,
                           "stuck_high": 0.0}, {"guard": guard}
        elif feats.slowest is None:
            probs, info = {"diverging": 0.0, "safe_to_accelerate": 0.0, "stagnating": 0.0,
                           "stuck_high": 0.0}, {"converged": True}
        else:
            probs, info = self.judge.judge(feats, verbal)

        # -------- judgments -> one move (thresholds live in code, not in the model)
        div = probs["diverging"]
        stall = n - self.stall_from >= max(5 * interval, 50) and u > 0.8 and probs["stuck_high"] > 0.6
        settle_after = self.settle_after or max(12 * interval, 300)
        settling = False
        if guard or (div > 0.6 and not self.frozen):
            move = "decrease_large" if div > 0.8 else "decrease_small"
            self.cooldown = 3
        elif self.frozen:
            move = "hold"
        elif feats.no_progress_iters >= settle_after:
            move, settling, self.frozen = "hold", True, True
        elif self.cooldown > 0:
            move = "hold"
            self.cooldown -= 1
        elif stall:
            move = "decrease_small"              # too large a pseudo-time step can stall convergence
            self.cooldown = 3
            self.stall_from = n
        elif div < 0.3 and probs["stagnating"] > 0.6:
            move = "increase_large"
        elif probs["safe_to_accelerate"] > 0.6:
            move = "increase_small"
        else:
            move = "hold"

        top = hi if self.ceiling is None else min(hi, self.ceiling)
        new_u = _step(u, MOVES[move], lo, top)
        if settling and self.best_u is not None:
            new_u = min(hi, max(lo, self.best_u))
        if (stall and move == "decrease_small") or guard:
            self.ceiling, self.ceiling_set_at = new_u, n      # do not climb straight back to where it went wrong

        # -------- upwind fallback: last resort when the factor is already at its floor
        upwind = upwind_now
        stop = False
        if cfg.get("upwind_fallback", True):
            if self.upwind_restored_at is not None and div > 0.6 and n - self.upwind_restored_at <= 4 * interval:
                upwind, self.upwind_sticky = True, True   # restoring the schemes brought the divergence back
            elif div > 0.8 and u <= lo + 1e-6:
                upwind = True
            if upwind_now:
                self.calm_with_upwind = self.calm_with_upwind + 1 if div < 0.3 else 0
                if not self.upwind_sticky and self.calm_with_upwind >= self.upwind_restore_after:
                    upwind, self.calm_with_upwind, self.upwind_restored_at = False, 0, n
        if div > 0.8 and u <= lo + 1e-6 and (upwind_now or not cfg.get("upwind_fallback", True)):
            self.floor_divergences += 1
            stop = bool(self.stop_after) and self.floor_divergences >= self.stop_after

        # -------- assemble the new factors
        equations: dict[str, float] = {}
        fields: dict[str, float] = {}
        if new_u != u or ctl["equations"].get(momentum) is None and move != "hold":
            equations[momentum] = new_u
            for f in cfg.get("followers", []):
                cur = ctl["equations"].get(f)
                equations[f] = new_u if settling else _step(hi if cur is None else float(cur), MOVES[move], lo, top)
            p_cur = ctl["fields"].get(pressure)
            if p_cur is not None and not ctl.get("consistent", False):
                fields[pressure] = round(min(P_BOUNDS[1], max(P_BOUNDS[0], 1.0 - new_u)), 4)   # SIMPLE rule
            slow = feats.slowest
            self.commanded = new_u
            self.last_change = {"iteration": n, "direction": "increased" if new_u > u else "decreased",
                                "s100_before": feats.fields[slow].s100 if slow else None}

        if new_u == u and move != "hold":
            note = f"hold ({move} wanted, but {momentum} = {u:.3f} is at its limit)"
        else:
            note = f"{move}: {momentum} {u:.3f} -> {new_u:.3f}"
        if settling:
            note = (f"settle: no new residual low for {feats.no_progress_iters} iterations, back to the best "
                    f"factor seen ({momentum} {u:.3f} -> {new_u:.3f}) and frozen")
        elif self.frozen and not guard:
            note = f"frozen at {momentum} = {u:.3f} (residual plateau)"
        if guard:
            note += f" [guard: {guard}]"
        if upwind != upwind_now:
            note += " [upwind fallback ON]" if upwind else " [upwind fallback OFF]"
        if self.upwind_sticky:
            note += " [upwind is permanent for this run]"
        if stop:
            note += " [stop: still diverging at the floor]"

        info.update(probs=probs, state=verbal, judge=self.judge.name, ceiling=self.ceiling, frozen=self.frozen,
                    no_progress_iters=feats.no_progress_iters, slowest=feats.slowest,
                    features={k: vars(v) for k, v in feats.fields.items()})
        return Decision(n, move, equations, fields, upwind, stop, note, info)
