"""Adaptive under-relaxation control of a running simpleFoamMod case with TypeSafe Jev.

Code owns the loop, the log parsing, the feature computation, the bounds, and the
hard safety guards. Jev supplies the judgment: every `--interval` iterations it sees
the current solver state and chooses how to move the U and p relaxation factors,
plus a separate yes/no read on whether the solution looks unstable.

The solver re-reads system/fvSolution at runtime (runTimeModifiable true), so a
change to the relaxationFactors block takes effect on the next iteration.

usage: jev_relax.py <run_dir> [--interval 10] [--api-key-file ../jev_api_key]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog import LogReader  # noqa: E402

from typesafe_sdk import Choice, Noul, TypeSafeClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Bounds enforced by code, whatever the model answers.
U_MIN, U_MAX = 0.5, 0.99
P_MIN, P_MAX = 0.2, 1.0

# U is moved in terms of its pseudo-time factor tau = a / (1 - a).
U_TAU_MULT = {"increase_large": 2.0, "increase_small": 1.4, "hold": 1.0,
              "decrease_small": 1 / 1.4, "decrease_large": 0.5}
P_STEP = {"increase_large": 0.2, "increase_small": 0.1, "hold": 0.0,
          "decrease_small": -0.1, "decrease_large": -0.2}

CONTEXT = (
    "Steady-state SIMPLEC (consistent) solver for incompressible creeping (Stokes, "
    "Reynolds number far below 1) flow of a viscous oil through a voxel-meshed fibre "
    "microstructure (17.5 million cells in production), started from a zero velocity "
    "field. The run exists only to compute the permeability of the structure. It stops "
    "automatically once the normalized slope of the last 10 permeability values drops "
    "below 0.01 and the predicted-vs-current permeability error drops below 0.01. "
    "Iterations are expensive, so the goal is to reach a converged, correct permeability "
    "in as few iterations as possible without causing oscillation or divergence. The "
    "velocity (U) under-relaxation factor acts like a pseudo time step: 0.9 corresponds "
    "to a pseudo-time factor of 9, 0.95 to 19, 0.99 to 99. Larger factors let the flow "
    "field develop faster but can destabilise the coupling. The pressure (p) factor "
    "relaxes the pressure correction; with SIMPLEC, values near 1 are usually well "
    "tolerated. Code applies hard bounds: U in [0.5, 0.995], p in [0.2, 1.0]."
)

ACTION_CRITERIA = {
    "increase_large": "The solution is clearly stable (residuals falling or flat, permeability "
                      "moving smoothly in one direction without oscillation), the permeability is "
                      "still far from settled, and the last change has had time to show its "
                      "effect; a substantially larger factor is safe and will save iterations.",
    "increase_small": "The solution is stable but some caution is warranted, for example the "
                      "effect of the last increase is only partly visible or residuals are "
                      "flattening; a modest increase is appropriate.",
    "hold": "The current factor is working well, the solution is already close to the stopping "
            "criterion, or the last change was too recent to judge its effect.",
    "decrease_small": "There are mild warning signs attributable to this factor, such as residuals "
                      "starting to grow, the permeability overshooting or wobbling slightly.",
    "decrease_large": "There is clear instability: residuals or continuity errors growing fast, "
                      "the permeability oscillating strongly or jumping erratically.",
}


def build_questions() -> dict:
    return {
        "u_action": Choice(
            instructions=(
                "Considering `solver` for the physics and goal, and the evidence in `current`, "
                "`recent_window`, `observations` and `action_history`, how should the velocity (U) "
                "under-relaxation factor be changed for the next window of iterations?"),
            criteria=ACTION_CRITERIA,
        ),
        "p_action": Choice(
            instructions=(
                "Considering `solver` for the physics and goal, and the evidence in `current`, "
                "`recent_window`, `observations` and `action_history`, how should the pressure (p) "
                "under-relaxation factor be changed for the next window of iterations?"),
            criteria=ACTION_CRITERIA,
        ),
        "unstable": Noul(
            instructions=(
                "Does the recent solver behaviour described in `recent_window` and `observations` "
                "show numerical instability, such as growing residuals or continuity errors, an "
                "oscillating or erratically jumping permeability, or a clear deterioration right "
                "after the last relaxation change?"),
            criteria={"true": "Instability or divergence is developing.",
                      "false": "The iteration is behaving smoothly."},
        ),
    }


# --------------------------------------------------------------------------- fvSolution I/O
RELAX_RE = {
    "U": re.compile(r"(equations\s*\{[^}]*?\bU\s+)([0-9.eE+-]+)(\s*;)", re.S),
    "p": re.compile(r"(fields\s*\{[^}]*?\bp\s+)([0-9.eE+-]+)(\s*;)", re.S),
}


def read_relax(case: Path) -> tuple[float, float]:
    txt = (case / "system/fvSolution").read_text()
    return (float(RELAX_RE["U"].search(txt).group(2)), float(RELAX_RE["p"].search(txt).group(2)))


RE_CONV = re.compile(r"(convPermeability\s+)(true|false)(\s*;)")


def write_relax(case: Path, u: float, p: float, conv: bool | None = None) -> None:
    """Write factors (and optionally the permeability stop switch) in ONE atomic replace, so the
    solver's time-stamp based re-read cannot miss one of two quick successive writes."""
    path = case / "system/fvSolution"
    txt = path.read_text()
    txt = RELAX_RE["U"].sub(lambda m: f"{m.group(1)}{u:.4f}{m.group(3)}", txt, count=1)
    txt = RELAX_RE["p"].sub(lambda m: f"{m.group(1)}{p:.4f}{m.group(3)}", txt, count=1)
    if conv is not None:
        txt = RE_CONV.sub(lambda m: f"{m.group(1)}{'true' if conv else 'false'}{m.group(3)}", txt, count=1)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(txt)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- features
def _lr(a: float | None, b: float | None) -> float | None:
    """log10 ratio b/a (negative = decreasing)."""
    if not a or not b or a <= 0 or b <= 0:
        return None
    return round(math.log10(b / a), 3)


def sign_changes(xs: list[float]) -> int:
    d = [b - a for a, b in zip(xs, xs[1:])]
    d = [x for x in d if x != 0]
    return sum(1 for a, b in zip(d, d[1:]) if (a > 0) != (b > 0))


def summarize(iters, w: int) -> dict:
    last = iters[-1]
    win = iters[-w:]
    k = [i.perm for i in win]
    kdiff_rel = (k[-1] - k[0]) / (abs(k[-1]) * max(len(k) - 1, 1)) if k[-1] else None
    prev = iters[-2 * w:-w] if len(iters) >= 2 * w else []
    prev_rate = None
    if len(prev) >= 2 and prev[-1].perm:
        prev_rate = (prev[-1].perm - prev[0].perm) / (abs(prev[-1].perm) * (len(prev) - 1))
    return {
        "iteration": last.it,
        "permeability_m2": f"{last.perm:.5e}",
        "relative_permeability_change_per_iteration": f"{kdiff_rel:.3e}" if kdiff_rel is not None else None,
        "same_quantity_previous_window": f"{prev_rate:.3e}" if prev_rate is not None else None,
        "stop_criterion_normalized_slope": last.slope, "stop_threshold_slope": 0.01,
        "stop_criterion_prediction_error": last.err, "stop_threshold_error": 0.01,
        "permeability_direction_reversals_in_window": sign_changes(k),
        "initial_residual_now": {f: last.res.get(f) for f in ("Ux", "Uy", "Uz", "p")},
        "log10_residual_change_over_window": {
            f: _lr(win[0].res.get(f), last.res.get(f)) for f in ("Ux", "p")},
        "continuity_error_now": last.cont,
        "log10_continuity_error_change_over_window": _lr(win[0].cont, last.cont),
        "linear_solver_iterations_now": {f: last.nit.get(f) for f in ("Ux", "p")},
    }


def observations(iters, w: int) -> list[str]:
    obs = []
    win = iters[-w:]
    k = [i.perm for i in win]
    rev = sign_changes(k)
    if rev == 0:
        obs.append(f"Permeability moved monotonically {'upward' if k[-1] > k[0] else 'downward'} "
                   f"over the last {len(k)} iterations.")
    else:
        obs.append(f"Permeability changed direction {rev} times in the last {len(k)} iterations.")
    for f in ("Ux", "p"):
        r = _lr(win[0].res.get(f), win[-1].res.get(f))
        if r is not None:
            obs.append(f"{f} initial residual {'fell' if r < 0 else 'rose'} by a factor of "
                       f"{10 ** abs(r):.2f} over the window.")
    lr = _lr(win[0].cont, win[-1].cont)
    if lr is not None:
        obs.append(f"Continuity error {'fell' if lr < 0 else 'rose'} by a factor of {10 ** abs(lr):.2f}.")
    s = iters[-1].slope
    if s is not None:
        obs.append(f"Stopping slope is {s:.4f} (needs < 0.01); "
                   f"{'close to' if s < 0.05 else 'far from'} the stopping criterion.")
    return obs


def hard_guard(iters, stable_ref: dict | None) -> str | None:
    """Code-level safety independent of the model. Returns a reason if the run must be rolled back."""
    last = iters[-1]
    vals = [last.perm, *last.res.values()]
    if any(v is None or not math.isfinite(v) for v in vals):
        return "non-finite value"
    if last.perm <= 0:
        return "non-positive permeability"
    if stable_ref:
        if last.res.get("Ux", 0) > 10 * stable_ref["ux"]:
            return "Ux residual grew >10x since last stable point"
        if last.cont and stable_ref["cont"] and last.cont > 10 * stable_ref["cont"]:
            return "continuity error grew >10x since last stable point"
    return None


# --------------------------------------------------------------------------- main loop
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("--interval", type=int, default=10, help="iterations between decisions")
    ap.add_argument("--start", type=int, default=10, help="first decision iteration")
    ap.add_argument("--api-key-file", type=Path, default=ROOT / "jev_api_key")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()

    if not os.environ.get("TYPESAFE_API_KEY"):
        os.environ["TYPESAFE_API_KEY"] = args.api_key_file.read_text().strip()

    case = args.case.resolve()
    log = LogReader(case / "log.simpleFoamMod")
    out = open(case / "jev_decisions.jsonl", "a")
    questions = build_questions()
    u, p = read_relax(case)
    history: list[dict] = []
    stable = {"u": u, "p": p, "ux": None, "cont": None}
    next_decision = args.start
    client = TypeSafeClient()
    idle = 0

    seen_restarts = 0
    before_crash: list = []
    guard_quiet_until = -1        # the guard fires at most once per decision interval
    resume_stop_at = None         # permeability stop test suspended after a decrease until this iteration
    while True:
        new = log.poll()
        if log.converged or (case / "supervisor_done").exists():
            break
        if len(log.restarts) > seen_restarts:
            # The supervisor restarted the solver from a checkpoint. Roll back only if the
            # iterations before the crash show real divergence; a crash out of a healthy
            # state is a machine fault and says nothing about the relaxation factors.
            r = log.restarts[-1]
            seen_restarts = len(log.restarts)
            diverging = residuals_diverging(before_crash)
            rec = {"iteration": r["restart_from"], "kind": "restart", "crashed_at": r["crashed_at"],
                   "diverging_before_crash": diverging, "t": time.time()}
            if diverging:
                nu_, np_ = max(U_MIN, min(stable["u"], u) - 0.05), max(P_MIN, min(stable["p"], p) - 0.1)
                write_relax(case, nu_, np_)
                rec.update({"from": [u, p], "to": [nu_, np_]})
                u, p = nu_, np_
                history.append({"iteration": r["restart_from"], "U": u, "p": p,
                                "note": "solver diverged and was restarted from a checkpoint with lower factors"})
            out.write(json.dumps(rec) + "\n"); out.flush()
            print(json.dumps(rec), flush=True)
            next_decision = max(next_decision, r["restart_from"] + args.interval)
        if not new:
            idle += 1
            time.sleep(args.poll)
            continue
        idle = 0
        it = log.iters
        before_crash = it[-12:]
        last = it[-1]

        if resume_stop_at is not None and last.it >= resume_stop_at:
            write_relax(case, u, p, conv=True)
            out.write(json.dumps({"iteration": last.it, "kind": "stop_test_resumed", "t": time.time()}) + "\n")
            out.flush()
            resume_stop_at = None

        reason = hard_guard(it, stable if stable["ux"] else None) if last.it >= guard_quiet_until else None
        if reason:
            nu_, np_ = max(U_MIN, min(stable["u"], u) - 0.05), max(P_MIN, min(stable["p"], p) - 0.1)
            # A smaller pseudo-time step makes the permeability change less per iteration, which the
            # per-iteration slope test would read as convergence: suspend the test for 2 intervals.
            write_relax(case, nu_, np_, conv=False)
            resume_stop_at = last.it + 2 * args.interval
            guard_quiet_until = last.it + args.interval
            # the post-rollback state becomes the new reference, so the guard cannot cascade
            stable = {"u": nu_, "p": np_, "ux": last.res.get("Ux"), "cont": last.cont}
            rec = {"iteration": last.it, "kind": "guard", "reason": reason,
                   "from": [u, p], "to": [nu_, np_], "t": time.time(), "stop_test_suspended_until": resume_stop_at}
            out.write(json.dumps(rec) + "\n"); out.flush()
            print(json.dumps(rec), flush=True)
            u, p = nu_, np_
            history.append({"iteration": last.it, "U": u, "p": p, "note": f"safety rollback: {reason}"})
            next_decision = last.it + args.interval
            continue

        if last.it < next_decision or len(it) < args.interval:
            continue

        state = {
            "solver": CONTEXT,
            "current": {"U_relaxation": u, "p_relaxation": p,
                        "iterations_since_last_change": last.it - (history[-1]["iteration"] if history else 0)},
            "recent_window": summarize(it, args.interval),
            "observations": observations(it, args.interval),
            "action_history": history[-6:],
        }
        t0 = time.time()
        resp, paused, attempt, outage = None, [], 0, 0.0
        while resp is None:
            try:
                resp = client.system_one(state=state, questions=questions)
            except Exception as e:  # service failure: freeze the solver until the API answers again
                attempt += 1
                if not paused:
                    paused = solver_pids(case)
                    for pid in paused:
                        os.kill(pid, signal.SIGSTOP)
                rec = {"iteration": last.it, "kind": "api_retry", "attempt": attempt, "paused": paused,
                       "error": repr(e)[:300], "t": time.time()}
                out.write(json.dumps(rec) + "\n"); out.flush()
                w = min(300, 2 * attempt)
                time.sleep(w)
                outage += w
        for pid in paused:
            os.kill(pid, signal.SIGCONT)
        lat = time.time() - t0 - outage
        ua, pa, un = resp.answers["u_action"], resp.answers["p_action"], resp.answers["unstable"]

        # Policy (code): an unstable read overrides any increase; low-confidence increases are softened.
        u_choice, p_choice = ua.choice, pa.choice
        if un.noul > 0.6:
            u_choice = "decrease_small" if un.noul < 0.8 else "decrease_large"
            p_choice = "decrease_small" if un.noul < 0.8 else "decrease_large"
        else:
            if u_choice == "increase_large" and ua.confidence < 0.5:
                u_choice = "increase_small"
            if p_choice == "increase_large" and pa.confidence < 0.5:
                p_choice = "increase_small"

        tau = u / (1 - u) * U_TAU_MULT[u_choice]
        nu_ = round(min(U_MAX, max(U_MIN, tau / (1 + tau))), 4)
        np_ = round(min(P_MAX, max(P_MIN, p + P_STEP[p_choice])), 4)

        # Remember the last point that looked healthy, for the hard guard.
        if un.noul < 0.4:
            stable = {"u": u, "p": p, "ux": last.res.get("Ux"), "cont": last.cont}

        changed = (nu_, np_) != (u, p)
        if changed:
            if nu_ < u or np_ < p:
                write_relax(case, nu_, np_, conv=False)
                resume_stop_at = last.it + 2 * args.interval
            else:
                write_relax(case, nu_, np_)
        rec = {
            "iteration": last.it, "kind": "decision", "t": t0, "latency_s": round(lat, 3),
            "from": [u, p], "to": [nu_, np_],
            "u_action": ua.choice, "u_probs": ua.probabilities, "u_conf": ua.confidence,
            "p_action": pa.choice, "p_probs": pa.probabilities, "p_conf": pa.confidence,
            "unstable": un.noul, "applied": [u_choice, p_choice],
            "usage": resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else str(resp.usage),
            "state": state,
        }
        out.write(json.dumps(rec) + "\n"); out.flush()
        print(f"it={last.it} K={last.perm:.4e} slope={last.slope} U {u}->{nu_} ({ua.choice} {ua.confidence:.2f}) "
              f"p {p}->{np_} ({pa.choice} {pa.confidence:.2f}) unstable={un.noul:.2f}", flush=True)
        if changed:
            history.append({"iteration": last.it, "U": nu_, "p": np_,
                            "rate_before": state["recent_window"]["relative_permeability_change_per_iteration"],
                            "residual_Ux_before": last.res.get("Ux")})
        elif history:
            history[-1]["later_check_iteration"] = last.it
            history[-1]["rate_later"] = state["recent_window"]["relative_permeability_change_per_iteration"]
            history[-1]["residual_Ux_later"] = last.res.get("Ux")
        u, p = nu_, np_
        next_decision = last.it + args.interval

    rec = {"kind": "end", "converged": log.converged, "fatal": log.fatal,
           "iteration": log.iters[-1].it if log.iters else None, "t": time.time()}
    out.write(json.dumps(rec) + "\n"); out.close()
    print(json.dumps(rec), flush=True)


def solver_pids(case: Path) -> list[int]:
    """PIDs of simpleFoamMod processes running in this case directory."""
    pids = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if "simpleFoamMod" in (d / "cmdline").read_bytes().decode(errors="ignore") and \
                    Path(os.readlink(d / "cwd")) == case:
                pids.append(int(d.name))
        except OSError:
            continue
    return pids


def residuals_diverging(iters) -> bool:
    """True if Ux or p initial residual rose more than 3x above its recent minimum."""
    if len(iters) < 3:
        return False
    for f in ("Ux", "p"):
        vals = [i.res.get(f) for i in iters if i.res.get(f)]
        if vals and vals[-1] > 3 * min(vals):
            return True
    return False


if __name__ == "__main__":
    main()
