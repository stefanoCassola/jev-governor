"""Run one steady OpenFOAM case with static or adaptive under-relaxation.

Methods
  static     fixed factors (case defaults or --relax U p [turb])
  heuristic  rule-based adaptive controller (residual trend / oscillation rules)
  jev        TypeSafe Jev chooses the move of the U and p factors every --interval iterations

Adaptive methods use a synchronous hand-off (jevSync function object): the solver blocks after
every --interval iterations until the controller has written the new factors, so iteration counts
do not depend on API latency. Code owns the features, bounds, and safety guards; Jev (or the rule
table) only picks one of five moves per factor.

usage: relaxctl.py <template_case> <run_dir> --method jev|heuristic|static [options]
Writes <run_dir>/result.json and decisions.jsonl.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog2 import FoamLog  # noqa: E402

SIF = "/work/cassola/openfoam/openfoam2312Mod.sif"
ROOT = Path(__file__).resolve().parents[2]          # /scratch/cassola/open_jev
MOVES = ["increase_large", "increase_small", "hold", "decrease_small", "decrease_large"]

# ----------------------------------------------------------------------------- case profile
# case.json in the template: {"solver": "simpleFoam", "algorithm": "SIMPLE"|"SIMPLEC",
#   "description": "...", "U": 0.7, "p": 0.3, "turb": 0.7 | null, "turb_fields": ["k", "epsilon"],
#   "targets": {"p": 1e-5, "U": 1e-6, ...}, "monitor_re": null, "monitor_name": null}


BOUNDS_OVERRIDE: dict | None = None   # set by --bounds
PCOUPLE = False                         # set by --pcouple: SIMPLE consistency alpha_p <= 1 - alpha_U


def bounds(algo: str) -> dict:
    if BOUNDS_OVERRIDE:
        return BOUNDS_OVERRIDE
    if algo == "SIMPLEC":
        return {"U": (0.5, 0.99), "p": (0.2, 1.0), "turb": (0.3, 0.99)}
    return {"U": (0.3, 0.95), "p": (0.05, 0.8), "turb": (0.3, 0.95)}


def tau(a: float) -> float:
    return a / (1.0 - a)


def from_tau(t: float) -> float:
    return t / (1.0 + t)


U_TAU_MULT = {"increase_large": 2.0, "increase_small": 1.4, "hold": 1.0,
              "decrease_small": 1 / 1.4, "decrease_large": 0.5}


def p_step(algo: str) -> dict:
    # SIMPLE: pressure factors live on a smaller scale (~1 - alpha_U), use multiplicative steps
    return {"increase_large": 0.2, "increase_small": 0.1, "hold": 0.0,
            "decrease_small": -0.1, "decrease_large": -0.2}


def apply_move(prof: dict, f: dict, mu: str, mp: str, scale: float = 1.0) -> dict:
    """Return new factors after moves mu (U, turbulence) and mp (p). scale<1 softens moves."""
    b = bounds(prof["algorithm"])
    m_u = U_TAU_MULT[mu] ** scale
    nu_ = min(b["U"][1], max(b["U"][0], from_tau(tau(f["U"]) * m_u)))
    if prof["algorithm"] == "SIMPLEC":
        np_ = f["p"] + p_step("SIMPLEC")[mp] * scale
    else:
        np_ = f["p"] * {"increase_large": 1.5, "increase_small": 1.2, "hold": 1.0,
                        "decrease_small": 1 / 1.2, "decrease_large": 1 / 1.5}[mp] ** scale
    np_ = min(b["p"][1], max(b["p"][0], np_))
    if PCOUPLE and prof["algorithm"] == "SIMPLE":
        np_ = max(b["p"][0], min(np_, 1.0 - nu_))
    out = {"U": round(nu_, 4), "p": round(np_, 4)}
    if f.get("turb") is not None and not prof.get("turb_follow", True):
        out["turb"] = f["turb"]
    elif f.get("turb") is not None:
        out["turb"] = round(min(b["turb"][1], max(b["turb"][0], from_tau(tau(f["turb"]) * m_u))), 4)
    else:
        out["turb"] = None
    return out


def write_relax(case: Path, f: dict, prof: dict) -> None:
    """Replace the relaxationFactors block in system/fvSolution."""
    path = case / "system/fvSolution"
    s = path.read_text()
    i = s.find("relaxationFactors")
    if i >= 0:
        j = s.index("{", i); d = 0
        for k in range(j, len(s)):
            d += s[k] == "{"; d -= s[k] == "}"
            if d == 0:
                break
        s = s[:i] + s[k + 1:]
    eq = f"        U               {f['U']};\n"
    if f.get("turb") is not None:
        eq += "".join(f"        {t:15s} {f['turb']};\n" for t in prof["turb_fields"])
    block = ("relaxationFactors\n{\n    fields\n    {\n        p               " + f"{f['p']};\n    }}\n"
             "    equations\n    {\n" + eq + "    }\n}\n")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(s.rstrip() + "\n\n" + block)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- features (code)
def trend_word(dec_per_10: float | None) -> str:
    """Residual trend in decades per 10 iterations -> words (negative = falling)."""
    if dec_per_10 is None or not math.isfinite(dec_per_10):
        return "unknown"
    if dec_per_10 < -0.5:
        return "falling fast"
    if dec_per_10 < -0.15:
        return "falling steadily"
    if dec_per_10 < -0.03:
        return "falling slowly"
    if dec_per_10 <= 0.03:
        return "flat (stalled)"
    if dec_per_10 <= 0.2:
        return "rising slowly"
    return "rising fast"


def osc_word(frac: float) -> str:
    if frac < 0.2:
        return "smooth"
    if frac < 0.45:
        return "mild oscillation"
    return "strong oscillation"


def distance_word(orders: float) -> str:
    if orders <= 0:
        return "already below its convergence target"
    if orders < 0.5:
        return "very close to its convergence target"
    if orders < 1.5:
        return "about one order of magnitude above its target"
    if orders < 3:
        return "a few orders of magnitude above its target"
    return "far above its target (many orders of magnitude)"


def field_features(iters, name: str, target: float | None, w: int) -> dict:
    vals = [i.res.get(name) for i in iters[-w:] if i.res.get(name)]
    if len(vals) < 3 or any(v <= 0 or not math.isfinite(v) for v in vals):
        return {"trend": "unknown", "trend_dec_per_10": None, "oscillation": "unknown", "orders_to_target": None}
    lv = [math.log10(v) for v in vals]
    n = len(lv)
    xm = (n - 1) / 2
    slope = sum((x - xm) * (y - sum(lv) / n) for x, y in enumerate(lv)) / sum((x - xm) ** 2 for x in range(n))
    d = [b - a for a, b in zip(lv, lv[1:])]
    flips = sum(1 for a, b in zip(d, d[1:]) if (a > 0) != (b > 0) and abs(a) + abs(b) > 0.02)
    frac = flips / max(len(d) - 1, 1)
    orders = math.log10(vals[-1] / target) if target else None
    return {"trend": trend_word(slope * 10), "trend_dec_per_10": round(slope * 10, 3),
            "oscillation": osc_word(frac), "orders_to_target": None if orders is None else round(orders, 2),
            "distance": distance_word(orders) if orders is not None else "no target"}


def features(iters, prof: dict, w: int) -> dict:
    fields = [f for f in prof["targets"]]
    out = {f: field_features(iters, f, prof["targets"][f], w) for f in fields}
    worst = max((v["orders_to_target"] for v in out.values() if v["orders_to_target"] is not None), default=None)
    return {"fields": out, "worst_orders": worst}


def effect_of_last_change(hist: list[dict]) -> str | None:
    """Compare the worst-field trend in the window before and after the last change (code, not model)."""
    for h in reversed(hist):
        if h.get("changed") and h.get("after_trend") is not None and h.get("before_trend") is not None:
            b, a = h["before_trend"], h["after_trend"]
            if a < b - 0.05:
                return f"after the last change ({h['move_desc']}) convergence became faster"
            if a > b + 0.05:
                return f"after the last change ({h['move_desc']}) convergence became slower"
            return f"after the last change ({h['move_desc']}) the convergence rate stayed about the same"
    return None


# ----------------------------------------------------------------------------- policies
class Heuristic:
    """Classical rule table (in the spirit of Min & Tao 2007) on the same features and move set:
    back off on clear divergence or strong oscillation, creep up while every residual falls, hold otherwise."""
    name = "heuristic"

    def decide(self, feats: dict, prof: dict, f: dict, hist: list) -> tuple[str, str, dict]:
        tr = [v["trend"] for v in feats["fields"].values()]
        osc = [v["oscillation"] for v in feats["fields"].values()]
        if "rising fast" in tr or "strong oscillation" in osc:
            m = "decrease_large"
        elif "rising slowly" in tr:
            m = "decrease_small"
        elif all(t.startswith("falling") for t in tr):
            m = "increase_small"
        else:
            m = "hold"
        return m, m, {}


ACTION_CRITERIA = {
    "increase_large": "Convergence is smooth and clearly stable (residuals falling or stalled without "
                      "any oscillation), the residuals are still far from their targets, and the last "
                      "change has had time to show its effect; a substantially larger factor is safe and "
                      "will save iterations.",
    "increase_small": "The iteration is stable but some caution is warranted, for example the effect "
                      "of the last increase is only partly visible, or convergence is slow but not "
                      "oscillating; a modest increase is appropriate.",
    "hold": "The current factor is working well, the last change was too recent to judge, or the "
            "residuals are already close to their targets.",
    "decrease_small": "There are mild warning signs attributable to this factor: mild oscillation, "
                      "residuals starting to rise, or convergence becoming slower after the last increase.",
    "decrease_large": "There is clear instability: residuals rising fast, strong oscillation, or a "
                      "clear deterioration right after the last increase.",
}


class JevPolicy:
    name = "jev"

    def __init__(self, model: str, use_unstable: bool = True):
        from typesafe_sdk import Choice, Noul, TypeSafeClient
        self.client = TypeSafeClient(model=model)
        self.use_unstable = use_unstable
        self.q = {
            "u_action": Choice(
                instructions=("Considering `solver` for the problem and goal, and the evidence in "
                              "`residuals`, `observations` and `recent_changes`, how should the momentum "
                              "(velocity U) under-relaxation factor be changed for the next window of "
                              "iterations?"),
                criteria=ACTION_CRITERIA),
            "p_action": Choice(
                instructions=("Considering `solver` for the problem and goal, and the evidence in "
                              "`residuals`, `observations` and `recent_changes`, how should the pressure (p) "
                              "under-relaxation factor be changed for the next window of iterations?"),
                criteria=ACTION_CRITERIA),
        }
        if use_unstable:
            self.q["unstable"] = Noul(
                instructions=("Do `residuals` and `observations` show that the iteration is becoming "
                              "numerically unstable, for example residuals rising fast, strong "
                              "oscillation, or a clear deterioration right after the last change?"),
                criteria={"true": "Instability or divergence is developing.",
                          "false": "The iteration is behaving acceptably."})

    def decide(self, feats: dict, prof: dict, f: dict, hist: list) -> tuple[str, str, dict]:
        b = bounds(prof["algorithm"])
        res = {}
        for name, v in feats["fields"].items():
            res[name] = f"{v['trend']}, {v['oscillation']}, {v.get('distance', '')}"
        obs = []
        e = effect_of_last_change(hist)
        if e:
            obs.append(e)
        since = hist_since_change(hist)
        obs.append(f"the factors were last changed {since} decision windows ago" if since is not None
                   else "the factors have not been changed yet")
        for k, lo_hi in (("U", b["U"]), ("p", b["p"])):
            lo, hi = lo_hi
            if f[k] >= hi - 1e-6:
                obs.append(f"the {k} factor is at its upper limit")
            elif f[k] <= lo + 1e-6:
                obs.append(f"the {k} factor is at its lower limit")
        state = {
            "solver": prof["context"],
            "current_factors": {"U": f["U"], "p": f["p"]},
            "residuals": res,
            "observations": obs,
            "recent_changes": [h["move_desc"] for h in hist if h.get("changed")][-4:],
        }
        t0 = time.time()
        r = None
        outage = 0.0
        attempt = 0
        while r is None:   # never skip a decision: the solver waits (sync hand-off) until the API answers
            try:
                r = self.client.system_one(state=state, questions=self.q)
            except Exception as ex:  # noqa: BLE001
                attempt += 1
                w = min(300, 2 * attempt)
                print(f"API error (attempt {attempt}), retry in {w}s: {ex!r}"[:400], file=sys.stderr, flush=True)
                time.sleep(w)
                outage += w
        info = {"latency_s": round(time.time() - t0 - outage, 3), "state": state}
        if attempt:
            info["api_retries"] = attempt
            info["api_outage_s"] = outage
        ua, pa = r.answers["u_action"], r.answers["p_action"]
        mu, mp = ua.choice, pa.choice
        info.update(u_probs=ua.probabilities, p_probs=pa.probabilities, u_conf=ua.confidence, p_conf=pa.confidence,
                    raw=[ua.choice, pa.choice], model=getattr(r, "model", None))
        if self.use_unstable:
            un = r.answers["unstable"].noul
            info["unstable"] = un
            if un > 0.6:
                mu = mp = "decrease_small" if un < 0.8 else "decrease_large"
        # code policy: soften low-confidence large increases
        if mu == "increase_large" and ua.confidence < 0.5:
            mu = "increase_small"
        if mp == "increase_large" and pa.confidence < 0.5:
            mp = "increase_small"
        return mu, mp, info


def hist_since_change(hist):
    for k, h in enumerate(reversed(hist)):
        if h.get("changed"):
            return k
    return None


# ----------------------------------------------------------------------------- run
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("template", type=Path)
    ap.add_argument("run", type=Path)
    ap.add_argument("--method", choices=["static", "heuristic", "jev", "heur3", "jev3", "heur4", "jev4"], required=True)
    ap.add_argument("--relax", type=float, nargs="+", help="initial U p [turb]")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--window", type=int, default=0, help="feature window (default = interval, min 5)")
    ap.add_argument("--scale", type=float, default=1.0, help="move magnitude scale")
    ap.add_argument("--no-unstable", action="store_true")
    ap.add_argument("--model", default="jev-1.13.0")
    ap.add_argument("--end", type=int, default=0, help="override endTime (iteration cap)")
    ap.add_argument("--set", action="append", default=[], help="file:sed-expression edits")
    ap.add_argument("--bounds", type=float, nargs=4, metavar=("UMIN", "UMAX", "PMIN", "PMAX"))
    ap.add_argument("--async", dest="async_", action="store_true",
                    help="asynchronous decisions (solver never waits; runTimeModifiable re-read)")
    ap.add_argument("--pcouple", action="store_true", help="SIMPLE: enforce alpha_p <= 1 - alpha_U")
    ap.add_argument("--tight", type=float, default=0, help="multiply all residualControl targets by this")
    a = ap.parse_args()

    global BOUNDS_OVERRIDE, PCOUPLE
    PCOUPLE = a.pcouple
    if a.bounds:
        BOUNDS_OVERRIDE = {"U": tuple(a.bounds[:2]), "p": tuple(a.bounds[2:]), "turb": (0.3, 0.99)}
    tpl, run = a.template.resolve(), a.run.resolve()
    if run.exists():
        shutil.rmtree(run)
    shutil.copytree(tpl, run, symlinks=True, ignore=shutil.ignore_patterns("constant"))
    (run / "constant").mkdir()
    for p in (tpl / "constant").iterdir():   # mesh read-only: symlink polyMesh, copy dictionaries
        (run / "constant" / p.name).symlink_to(p) if p.name == "polyMesh" else \
            (shutil.copytree(p, run / "constant" / p.name) if p.is_dir() else shutil.copy(p, run / "constant"))
    prof = json.loads((tpl / "case.json").read_text())
    for s in a.set:
        fn, expr = s.split(":", 1)
        subprocess.run(["sed", "-i", expr, str(run / fn)], check=True)
    if a.tight:
        fv = run / "system/fvSolution"
        txt = fv.read_text()
        i = txt.index("residualControl"); j = txt.index("}", i)
        blk = re.sub(r"(\s)([0-9.]+e?-?[0-9]*)\s*;", lambda m: f"{m.group(1)}{float(m.group(2)) * a.tight:g};",
                     txt[i:j])
        fv.write_text(txt[:i] + blk + txt[j:])
        prof["targets"] = {k: v * a.tight for k, v in prof["targets"].items()}
    if a.end:
        subprocess.run(["sed", "-i", f"s/^endTime .*/endTime         {a.end};/", str(run / "system/controlDict")], check=True)

    cd = run / "system/controlDict"
    end = re.search(r"^endTime\s+(\d+);", cd.read_text(), re.M).group(1)
    subprocess.run(["sed", "-i", f"s/^writeInterval .*/writeInterval   {end};/", str(cd)], check=True)
    f = {"U": prof["U"], "p": prof["p"], "turb": prof.get("turb")}
    if a.relax:
        f["U"], f["p"] = a.relax[0], a.relax[1]
        if f["turb"] is not None:
            f["turb"] = a.relax[2] if len(a.relax) > 2 else (
                prof["turb"] if not prof.get("turb_follow", True) else
                min(0.99, max(0.3, a.relax[0] + (prof["turb"] - prof["U"]))))
    write_relax(run, f, prof)
    adaptive = a.method != "static"
    (run / "sync").mkdir(exist_ok=True)
    (run / "sync/interval").write_text(f"{a.interval if (adaptive and not a.async_) else 0}\n")
    if a.async_:
        (run / "sync" / "async").touch()

    policy = None
    if a.method == "jev":
        if not os.environ.get("TYPESAFE_API_KEY"):
            os.environ["TYPESAFE_API_KEY"] = (ROOT / "jev_api_key").read_text().strip()
        policy = JevPolicy(a.model, use_unstable=not a.no_unstable)
    elif a.method == "heuristic":
        policy = Heuristic()
    ctl3 = None
    if a.method in ("jev3", "heur3", "jev4", "heur4"):
        import ctl3 as C3
        if a.method in ("jev3", "jev4"):
            if not os.environ.get("TYPESAFE_API_KEY"):
                os.environ["TYPESAFE_API_KEY"] = (ROOT / "jev_api_key").read_text().strip()
            judge = C3.Jev3(a.model)
        else:
            judge = C3.Heur3()
        ttl = max(10 * a.interval, 100) if a.method.endswith("4") else None
        ctl3 = C3.Controller3(judge, prof, bounds(prof["algorithm"]), a.interval, ceiling_ttl=ttl)
    w = a.window or max(a.interval, 5)

    logp = run / "log.simpleFoam"
    t_start = time.time()
    with open(logp, "w") as lf:
        proc = subprocess.Popen(["stdbuf", "-oL", "-eL", "apptainer", "exec", "--bind", ",".join(sorted({"/scratch", "/work", "/" + run.parts[1]})), SIF,
                                 "openfoam2312", prof.get("solver", "simpleFoam"), "-case", str(run)],
                                stdout=lf, stderr=subprocess.STDOUT)
    log = FoamLog(logp, prof.get("monitor_re"))
    dec = open(run / "decisions.jsonl", "w")
    hist: list[dict] = []
    wait_total = 0.0
    n_dec = 0

    def decide_at(n: int, it: list) -> None:
        """One controller decision on the history up to iteration n; writes fvSolution if changed."""
        nonlocal f, n_dec
        if ctl3 is not None:
            nf, extra = ctl3.decide(it, n, f, hist)
            changed = (nf["U"], nf["p"]) != (f["U"], f["p"])
            rec = {"iteration": n, "from": dict(f), "to": nf, **extra["info"]}
            if changed:
                write_relax(run, nf, prof)
                if a.async_:
                    (run / "sync" / "update").touch()
                f = nf
            dec.write(json.dumps(rec) + "\n"); dec.flush()
            hist.append(extra["hist"])
            n_dec += 1
            return
        feats = features(it, prof, w)
        worst = worst_trend(feats)
        if hist:
            hist[-1]["after_trend"] = worst
        mu, mp, info = policy.decide(feats, prof, f, hist)
        nf = apply_move(prof, f, mu, mp, a.scale)
        changed = (nf["U"], nf["p"]) != (f["U"], f["p"])
        desc = (f"U {f['U']}->{nf['U']}, p {f['p']}->{nf['p']} at iteration {n}" if changed else None)
        rec = {"iteration": n, "from": dict(f), "to": nf, "u_move": mu, "p_move": mp, "features": feats, **info}
        if changed:
            write_relax(run, nf, prof)
            if a.async_:
                (run / "sync" / "update").touch()
            f = nf
        dec.write(json.dumps(rec) + "\n"); dec.flush()
        hist.append({"iteration": n, "changed": changed, "move_desc": desc, "before_trend": worst,
                     "after_trend": None})
        n_dec += 1

    if a.async_:
        # Solver never waits: decide on the latest complete iteration once the next decision point is
        # reached; OpenFOAM re-reads fvSolution by itself (runTimeModifiable). Decision time overlaps
        # with solver iterations, so the new factors act a few iterations late.
        next_n = a.interval
        while True:
            rc = proc.poll()
            log.poll()
            if rc is not None:
                break
            if not log.iters or log.iters[-1].it < next_n or log.converged:
                time.sleep(0.02)
                continue
            n = log.iters[-1].it
            decide_at(n, list(log.iters))
            next_n = n + a.interval
    else:
        served = set()
        while True:
            rc = proc.poll()
            waits = sorted(int(p.name[5:]) for p in (run / "sync").glob("wait_*"))
            pending = [n for n in waits if n not in served]
            if not pending:
                if rc is not None:
                    break
                time.sleep(0.01)
                continue
            n = pending[0]
            tw = time.time()
            log.complete_through(n)
            it = log.upto(n)
            if not (log.converged or not it):
                decide_at(n, it)
            (run / "sync" / f"go_{n}").touch()
            served.add(n)
            wait_total += time.time() - tw
    log.poll()
    last = log.iters[-1] if log.iters else None
    result = {
        "template": tpl.name, "run": run.name, "method": a.method, "interval": a.interval if adaptive else 0,
        "scale": a.scale, "init": a.relax, "final_factors": f, "rc": rc, "converged": log.converged,
        "fatal": log.fatal or rc != 0, "iterations": last.it if last else 0,
        "exec_s": last.exec_s if last else None, "clock_s": last.clock_s if last else None,
        "wall_s": round(time.time() - t_start, 2), "controller_wait_s": round(wait_total, 2),
        "decisions": n_dec, "host": os.uname().nodename, "async": a.async_,
        "rereads": len(log.rereads), "async_applied_at": log.async_applied,
        "final_res": last.res if last else None, "monitor": last.monitor if last else None,
    }
    (run / "result.json").write_text(json.dumps(result, indent=1))
    # keep only the final time directory's fields; drop the sync files
    shutil.rmtree(run / "sync", ignore_errors=True)
    print(json.dumps(result))


def worst_trend(feats: dict) -> float | None:
    """Slowest (largest) residual trend in decades/10 it over controlled fields."""
    ts = [v["trend_dec_per_10"] for v in feats["fields"].values() if v["trend_dec_per_10"] is not None]
    return max(ts) if ts else None


if __name__ == "__main__":
    main()
