"""Controller v3: transient-aware features, atomic yes/no judgments, textbook pressure rule.

Design (after the failure analysis of v1/v2, see study/results/tables/tab_first_design.md):
- Only the momentum factor alpha_U (pseudo-time step) is controlled by judgment. The pressure factor
  follows the textbook rule in code: SIMPLE alpha_p = 1 - alpha_U, SIMPLEC alpha_p = 1.
- Features are computed on a window of W = max(2N, 20) iterations that excludes the first SETTLE
  iterations after a factor change, so the short residual bump that every change causes is not
  mistaken for a trend. They focus on the slowest-converging equation.
- The judgment is three calibrated yes/no questions (diverging? safe to accelerate? stagnating?);
  code maps them to one of five moves, with a cooldown after any decrease and a hard guard.
- The rule-based baseline (Heur3) uses the same features, the same mapping and the same guard;
  only the three judgments come from fixed thresholds instead of the model.
"""
from __future__ import annotations

import math
import sys
import time

SETTLE = 5
U_MULT = {"increase_large": 2.0, "increase_small": 1.4, "hold": 1.0, "decrease_small": 1 / 1.4,
          "decrease_large": 0.5}


def _slope(vals: list[float]) -> float:
    """Least-squares slope of log10(vals) per iteration."""
    lv = [math.log10(v) for v in vals]
    n = len(lv)
    xm = (n - 1) / 2
    ym = sum(lv) / n
    den = sum((x - xm) ** 2 for x in range(n))
    return sum((x - xm) * (y - ym) for x, y in enumerate(lv)) / den if den else 0.0


def _osc(vals: list[float]) -> float:
    lv = [math.log10(v) for v in vals]
    d = [b - a for a, b in zip(lv, lv[1:])]
    flips = sum(1 for a, b in zip(d, d[1:]) if (a > 0) != (b > 0) and abs(a) + abs(b) > 0.02)
    return flips / max(len(d) - 1, 1)


def trend_word(s100: float) -> str:
    """Trend of a residual in decades per 100 iterations (negative = falling)."""
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


def features(iters, prof: dict, n: int, last_change: int | None, N: int) -> dict:
    W = max(2 * N, 20)
    start = n - W
    if last_change is not None:
        start = max(start, last_change + SETTLE)
    win = [i for i in iters if start < i.it <= n]
    if len(win) < 5:
        win = iters[-5:]
    fields = {}
    for f, tgt in prof["targets"].items():
        vals = [i.res.get(f) for i in win if i.res.get(f)]
        if len(vals) < 3 or any((not math.isfinite(v)) or v <= 0 for v in vals):
            fields[f] = {"bad": True}
            continue
        s100 = _slope(vals) * 100
        fields[f] = {"s100": round(s100, 3), "trend": trend_word(s100), "osc": osc_word(_osc(vals)),
                     "orders": round(math.log10(vals[-1] / tgt), 2) if tgt else 0.0,
                     "rise": round(math.log10(max(vals) / vals[0]), 3), "now": vals[-1]}
    active = {f: v for f, v in fields.items() if not v.get("bad") and v["orders"] > 0}
    slow = max(active, key=lambda f: active[f]["orders"]) if active else None
    return {"window": [win[0].it, win[-1].it], "fields": fields, "slowest": slow,
            "bad": any(v.get("bad") for v in fields.values())}


def worst_level(it, prof: dict) -> float | None:
    """Largest log10(residual/target) over all equations at one iteration (overall distance to done)."""
    vals = [math.log10(it.res[f] / t) for f, t in prof["targets"].items()
            if it.res.get(f) and it.res[f] > 0 and t]
    return max(vals) if vals else None


def verbal_state(feats: dict, prof: dict, f: dict, hist: list, b: dict) -> dict:
    fl = feats["fields"]
    s = feats["slowest"]
    res = {}
    if s is None:
        res["overall"] = "all residuals are already at or below their convergence targets"
    else:
        v = fl[s]
        dist = ("far above (more than two orders of magnitude)" if v["orders"] > 2 else
                "one to two orders of magnitude above" if v["orders"] > 1 else "less than one order of magnitude above")
        res["slowest_equation"] = f"{s}: {v['trend']}, {v['osc']}, {dist} its target"
    others = []
    for name, v in fl.items():
        if name == s or v.get("bad"):
            continue
        if v["orders"] <= 0:
            others.append(f"{name}: already below target")
        else:
            others.append(f"{name}: {v['trend']}, {v['osc']}")
    res["other_equations"] = others
    obs = []
    last = next((h for h in reversed(hist) if h["changed"]), None)
    if last is None:
        obs.append("the factor has not been changed yet")
    else:
        obs.append(f"the momentum factor was {last['direction']} {feats['window'][1] - last['iteration']} "
                   "iterations ago; the iterations right after that change are excluded from this window")
        if last.get("s100_before") is not None and s is not None and fl[s].get("s100") is not None:
            d = fl[s]["s100"] - last["s100_before"]
            obs.append("since that change the slowest residual converges " +
                       ("faster" if d < -0.1 else "slower" if d > 0.1 else "at about the same rate"))
    npi = feats.get("no_progress_iters")
    if npi is not None and npi >= 20:
        obs.append(f"the overall residual level has not reached a new low for {npi} iterations")
    if f["U"] >= b["U"][1] - 1e-6:
        obs.append("the momentum factor is at its upper limit")
    if f["U"] <= b["U"][0] + 1e-6:
        obs.append("the momentum factor is at its lower limit")
    ctx = prof.get("context3") or (prof["context"] + " The pressure under-relaxation factor is set automatically "
                                   "from the momentum factor; only the momentum factor is decided.")
    return {"solver": ctx, "momentum_factor": f["U"], "residuals": res, "observations": obs}


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


class Jev3:
    name = "jev3"

    def __init__(self, model: str):
        from typesafe_sdk import Noul, TypeSafeClient
        self.client = TypeSafeClient(model=model)
        self.q = {k: Noul(instructions=t, criteria=c) for k, (t, c) in Q_TEXT.items()}

    def judge(self, feats, prof, f, hist, b) -> tuple[dict, dict]:
        state = verbal_state(feats, prof, f, hist, b)
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


class Heur3:
    name = "heur3"

    def judge(self, feats, prof, f, hist, b) -> tuple[dict, dict]:
        fl = feats["fields"]
        s = feats["slowest"]
        act = [v for v in fl.values() if not v.get("bad") and v["orders"] > 0]
        rising = any(v["s100"] > 0.05 and v["rise"] > 0.3 for v in act)
        strong = any(v["osc"] == "strong oscillation" for v in act)
        div = 1.0 if (any(v["s100"] > 0.5 and v["rise"] > 1.0 for v in act)) else (0.7 if (rising or strong) else 0.0)
        safe = 1.0 if not (rising or strong) else 0.0
        stag = 1.0 if (s is not None and fl[s]["s100"] > -0.3) else 0.0
        stuck = 1.0 if (feats.get("no_progress_iters") or 0) >= 100 else 0.0
        return {"diverging": div, "safe_to_accelerate": safe, "stagnating": stag, "stuck_high": stuck}, {}


def p_rule(prof: dict, u: float, b: dict) -> float:
    if prof["algorithm"] == "SIMPLE":
        return round(min(b["p"][1], max(b["p"][0], 1.0 - u)), 4)
    return round(min(b["p"][1], 1.0), 4)


class Controller3:
    """Maps judgments to factors; shared by Jev3 and Heur3."""

    def __init__(self, judge, prof: dict, b: dict, N: int):
        self.judge, self.prof, self.b, self.N = judge, prof, b, N
        self.cooldown = 0
        self.last_change: int | None = None
        self.last_res: dict | None = None
        self.best_level: float | None = None
        self.best_it = 0
        self.ceiling: float | None = None
        self.seen = 0

    def decide(self, iters, n: int, f: dict, hist: list) -> tuple[dict, dict]:
        feats = features(iters, self.prof, n, self.last_change, self.N)
        for it in iters[self.seen:]:
            lv = worst_level(it, self.prof)
            if lv is not None and (self.best_level is None or lv < self.best_level - 0.1):
                self.best_level, self.best_it = lv, it.it
        self.seen = len(iters)
        feats["no_progress_iters"] = n - self.best_it
        guard = None
        now = iters[-1].res if iters else {}
        if feats["bad"]:
            guard = "non-finite residual"
        elif self.last_res:
            for k, v in now.items():
                if k in self.last_res and self.last_res[k] and v > 10 * self.last_res[k] and v > 1e-3:
                    guard = f"{k} residual rose more than tenfold since the last decision"
        self.last_res = dict(now)
        if guard:
            probs, info = {"diverging": 1.0, "safe_to_accelerate": 0.0, "stagnating": 0.0, "stuck_high": 0.0}, \
                {"guard": guard}
        else:
            probs, info = self.judge.judge(feats, self.prof, f, hist, self.b)
        if probs["diverging"] > 0.6:
            move = "decrease_large" if probs["diverging"] > 0.8 else "decrease_small"
            self.cooldown = 3
        elif self.cooldown > 0:
            move = "hold"
            self.cooldown -= 1
        elif feats["no_progress_iters"] >= max(5 * self.N, 50) and f["U"] > 0.8 and probs.get("stuck_high", 0) > 0.6:
            move = "decrease_small"      # too large a pseudo-time step stalls convergence: back off, cap
            self.cooldown = 3
            self.best_it = n             # restart the stall clock
        elif probs["diverging"] < 0.3 and probs["stagnating"] > 0.6:
            move = "increase_large"      # clearly stable but stalled: take a much larger pseudo-time step
        elif probs["safe_to_accelerate"] > 0.6:
            move = "increase_small"
        else:
            move = "hold"
        tau = f["U"] / (1 - f["U"]) * U_MULT[move]
        hi = self.b["U"][1] if self.ceiling is None else min(self.b["U"][1], self.ceiling)
        nu = round(min(hi, max(self.b["U"][0], tau / (1 + tau))), 4)
        if move == "decrease_small" and probs.get("stuck_high", 0) > 0.6 and not probs["diverging"] > 0.6:
            self.ceiling = nu            # do not climb back into the stalling range
        nf = {"U": nu, "p": p_rule(self.prof, nu, self.b), "turb": f.get("turb")}
        if f.get("turb") is not None and self.prof.get("turb_follow", True):
            tt = f["turb"] / (1 - f["turb"]) * U_MULT[move]
            nf["turb"] = round(min(0.99, max(0.3, tt / (1 + tt))), 4)
        changed = (nf["U"], nf["p"]) != (f["U"], f["p"])
        s = feats["slowest"]
        s100 = feats["fields"][s]["s100"] if s and not feats["fields"][s].get("bad") else None
        if changed:
            self.last_change = n
        rec_hist = {"iteration": n, "changed": changed, "s100_before": s100,
                    "direction": "increased" if nf["U"] > f["U"] else "decreased"}
        info.update(move=move, probs=probs, features=feats, u_move=move, p_move="rule", ceiling=self.ceiling)
        return nf, {"info": info, "hist": rec_hist}
