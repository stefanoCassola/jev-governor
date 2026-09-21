"""Residual features and the verbal state handed to the judge.

Jev reads text, not ordered quantities, so all numeric work happens here: trends are
least-squares slopes of log10(residual), and the judge only sees words such as
"falling steadily" or "strong oscillation".

The feature design (settle window after a change, focus on the slowest equation) follows
the relaxation-control study in study/ of this repository.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SETTLE = 5  # iterations ignored after a change: every change causes a short residual bump


@dataclass
class Sample:
    it: int
    res: dict[str, float | None]


@dataclass
class FieldFeatures:
    bad: bool = False
    s100: float = 0.0      # slope in decades per 100 iterations (negative = falling)
    trend: str = ""
    osc: str = ""
    orders: float = 0.0    # log10(residual / target); <= 0 means converged
    rise: float = 0.0      # log10(max / first) inside the window
    now: float = 0.0


@dataclass
class Features:
    window: tuple[int, int]
    fields: dict[str, FieldFeatures]
    slowest: str | None
    bad: bool
    no_progress_iters: int = 0
    extra: dict = field(default_factory=dict)


def slope(vals: list[float]) -> float:
    """Least-squares slope of log10(vals) per iteration."""
    lv = [math.log10(v) for v in vals]
    n = len(lv)
    xm = (n - 1) / 2
    ym = sum(lv) / n
    den = sum((x - xm) ** 2 for x in range(n))
    return sum((x - xm) * (y - ym) for x, y in enumerate(lv)) / den if den else 0.0


OSC_AMPLITUDE = 0.1  # decades; smaller wiggles are the noise of a residual plateau, not an instability


def oscillation(vals: list[float]) -> float:
    """Fraction of sign flips between successive changes of log10(vals), ignoring small wiggles."""
    lv = [math.log10(v) for v in vals]
    d = [b - a for a, b in zip(lv, lv[1:])]
    flips = sum(1 for a, b in zip(d, d[1:]) if (a > 0) != (b > 0) and abs(a) + abs(b) > OSC_AMPLITUDE)
    return flips / max(len(d) - 1, 1)


def trend_word(s100: float) -> str:
    if s100 < -1.0:
        return "falling fast"
    if s100 < -0.3:
        return "falling steadily"
    if s100 < -0.05:
        return "falling slowly"
    if s100 <= 0.05:
        return "stagnating (not falling)"
    if s100 <= 0.5:
        return "rising slowly"
    return "rising fast"


def osc_word(fr: float) -> str:
    return "smooth" if fr < 0.2 else ("mild oscillation" if fr < 0.45 else "strong oscillation")


def compute(samples: list[Sample], targets: dict[str, float], n: int, last_change: int | None,
            interval: int, default_target: float = 1e-5) -> Features:
    width = max(2 * interval, 20)
    start = n - width
    if last_change is not None:
        start = max(start, last_change + SETTLE)
    win = [s for s in samples if start < s.it <= n]
    if len(win) < 5:
        win = samples[-5:]

    fields: dict[str, FieldFeatures] = {}
    for name, tgt in targets.items():
        tgt = tgt or default_target
        raw = [s.res.get(name) for s in win]
        if any(v is None or not math.isfinite(v) for v in raw):
            fields[name] = FieldFeatures(bad=True)
            continue
        vals = [v for v in raw if v > 0]           # -1 marks "not solved this iteration"
        if len(vals) < 3:
            continue
        s100 = slope(vals) * 100
        fields[name] = FieldFeatures(
            s100=round(s100, 3), trend=trend_word(s100), osc=osc_word(oscillation(vals)),
            orders=round(math.log10(vals[-1] / tgt), 2),
            rise=round(math.log10(max(vals) / vals[0]), 3), now=vals[-1])

    active = {k: v for k, v in fields.items() if not v.bad and v.orders > 0}
    slowest = max(active, key=lambda k: active[k].orders) if active else None
    return Features(window=(win[0].it, win[-1].it) if win else (n, n), fields=fields,
                    slowest=slowest, bad=any(v.bad for v in fields.values()))


def worst_level(sample: Sample, targets: dict[str, float], default_target: float = 1e-5) -> float | None:
    """Largest log10(residual / target) over all equations: overall distance to convergence."""
    vals = [math.log10(v / (targets.get(k) or default_target)) for k, v in sample.res.items()
            if v is not None and math.isfinite(v) and v > 0]
    return max(vals) if vals else None


def verbal_state(feats: Features, *, description: str, momentum: str, factor: float,
                 bounds: tuple[float, float], last_change: dict | None, upwind: bool) -> dict:
    """The state sent to the judge: words for every quantity that needs ordering."""
    fl = feats.fields
    s = feats.slowest
    res: dict = {}
    if s is None:
        res["overall"] = "all residuals are already at or below their convergence targets"
    else:
        v = fl[s]
        dist = ("far above (more than two orders of magnitude)" if v.orders > 2 else
                "one to two orders of magnitude above" if v.orders > 1 else
                "less than one order of magnitude above")
        res["slowest_equation"] = f"{s}: {v.trend}, {v.osc}, {dist} its target"
    others = []
    for name, v in fl.items():
        if name == s or v.bad:
            continue
        others.append(f"{name}: already below target" if v.orders <= 0 else f"{name}: {v.trend}, {v.osc}")
    res["other_equations"] = others

    obs = []
    if last_change is None:
        obs.append("the momentum under-relaxation factor has not been changed yet")
    else:
        obs.append(f"the momentum factor was {last_change['direction']} "
                   f"{feats.window[1] - last_change['iteration']} iterations ago; the iterations right "
                   "after that change are excluded from this window")
        before = last_change.get("s100_before")
        if before is not None and s is not None:
            d = fl[s].s100 - before
            obs.append("since that change the slowest residual converges " +
                       ("faster" if d < -0.1 else "slower" if d > 0.1 else "at about the same rate"))
    if feats.no_progress_iters >= 20:
        obs.append(f"the overall residual level has not reached a new low for "
                   f"{feats.no_progress_iters} iterations")
    if factor >= bounds[1] - 1e-6:
        obs.append("the momentum factor is at its upper limit")
    if factor <= bounds[0] + 1e-6:
        obs.append("the momentum factor is at its lower limit")
    if upwind:
        obs.append("a first-order upwind fallback for the convective schemes is currently active")

    solver = (description or "Steady-state incompressible CFD solved with the SIMPLE algorithm.") + \
        (f" Only the under-relaxation factor of the {momentum} (momentum) equation is decided; "
         "other factors follow it by fixed rules. A higher factor means a larger pseudo-time step: "
         "faster convergence but less stability.")
    return {"solver": solver, "momentum_factor": factor, "residuals": res, "observations": obs}
