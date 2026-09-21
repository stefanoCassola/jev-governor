"""File-based hand-off with the jevGovernor function object.

The solver writes jevGovernor/state_<n>.json; the sidecar answers with jevGovernor/decision_<n>
(written to a temporary name and renamed, so a visible file is always complete) and appends
one line per decision to jevGovernor/decisions.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .judges import BackendUnavailable, make_judge
from .policy import Decision, Governor
from .transient import GovernorT

STATE_RE = re.compile(r"^state_(\d+)\.json$")


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_visible(pid: int | None) -> bool:
    """True if the solver's PID can be seen from here (not the case across hosts or PID namespaces)."""
    return bool(pid) and Path(f"/proc/{pid}").exists()


def steps_summary(handoff: Path) -> dict | None:
    """Totals of a PIMPLE-mode run from the steps.csv the function object writes."""
    path = handoff / "steps.csv"
    if not path.exists():
        return None
    rows = [line.split(",") for line in path.read_text().splitlines()[1:] if line.strip()]
    if not rows:
        return None
    return {"steps": len(rows), "outer_total": sum(int(r[2]) for r in rows),
            "slow_capped_steps": sum(1 for r in rows if r[3] == "slow_capped"),
            "unstable_steps": sum(1 for r in rows if r[3] == "unstable"),
            "final_factors": {"U": float(rows[-1][4]), "p": float(rows[-1][5])}}


def write_summary(handoff: Path, completed: bool, n_decisions: int) -> None:
    """jevGovernor/summary.json: one small file a batch runner can read instead of the solver log."""
    out = {"completed": completed, "decisions": n_decisions}
    out.update(steps_summary(handoff) or {})
    tmp = handoff / ".summary.tmp"
    tmp.write_text(json.dumps(out, indent=1) + "\n")
    tmp.rename(handoff / "summary.json")


def write_decision(handoff: Path, decision: Decision) -> None:
    tmp = handoff / f".decision_{decision.iteration}.tmp"
    tmp.write_text(decision.to_foam())
    tmp.rename(handoff / f"decision_{decision.iteration}")


def log_decision(handoff: Path, decision: Decision, wall_s: float) -> None:
    rec = {"iteration": decision.iteration, "move": decision.move, "equations": decision.equations,
           "fields": decision.fields, "upwind": decision.upwind, "stop": decision.stop,
           "note": decision.note, "decision_wall_s": round(wall_s, 4), **decision.info}
    with open(handoff / "decisions.jsonl", "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def serve(case: Path, *, backend: str | None = None, pid: int | None = None, poll_s: float = 0.005,
          governor=None, max_idle_s: float | None = None, model: str | None = None,
          idle_timeout_s: float | None = None) -> int:
    """Answer state files until the solver is done. Returns the number of decisions."""
    handoff = case / "jevGovernor"
    handoff.mkdir(exist_ok=True)
    done: set[int] = set()
    broken: str | None = None
    backend = backend or os.environ.get("JEV_GOVERNOR_BACKEND")
    if backend == "jev":                       # import the SDK before the first state arrives
        try:
            import typesafe_sdk  # noqa: F401
        except Exception:  # noqa: BLE001  reported with the first state
            pass
    solver_pid = pid
    last_state = None
    n_decisions = 0
    idle_since = time.time()
    started = time.time() - 1.0                # a "done" file of an earlier run in this directory does not count

    while True:
        todo = sorted(int(m.group(1)) for f in os.listdir(handoff) if (m := STATE_RE.match(f))
                      and int(m.group(1)) not in done)
        for n in todo:
            t0 = time.time()
            state = json.loads((handoff / f"state_{n}.json").read_text())
            if state["config"].get("mode") == "async" and n != todo[-1] and governor is not None:
                governor.observe(state)        # a newer state is waiting: keep the samples, decide on that one
                done.add(n)
                continue
            last_state = time.time()
            if solver_pid is None and pid_visible(state.get("solver_pid")):
                solver_pid = state["solver_pid"]          # same PID namespace: notice a crashed solver
            if governor is None and broken is None:   # the function object dictionary holds the settings
                cfg = state["config"]
                try:
                    algorithm = cfg.get("algorithm", "SIMPLE")
                    judge = make_judge(backend or cfg.get("backend", "rules"), model or cfg.get("model", "jev-1.13.0"),
                                       algorithm)
                    governor = (GovernorT(judge, memory=handoff / "memory.json") if algorithm == "PIMPLE"
                                else Governor(judge))
                except Exception as ex:  # noqa: BLE001  missing SDK or key: say so, do not hang the solver
                    broken = f"backend unavailable, run is NOT governed: {ex}"[:400]
                    print(f"jev-governor: {broken}", flush=True)
            if broken is None:
                try:
                    decision = governor.decide(state)
                except BackendUnavailable as ex:   # e.g. the key was rejected at the first request
                    broken = f"backend unavailable, run is NOT governed: {ex}"[:400]
                    print(f"jev-governor: {broken}", flush=True)
            if broken is not None:
                decision = Decision(n, "hold", {}, {}, bool(state["controls"]["upwind"]), False, broken)
            write_decision(handoff, decision)
            log_decision(handoff, decision, time.time() - t0)
            print(f"[{n}] {decision.note}", flush=True)
            done.add(n)
            n_decisions += 1
            idle_since = time.time()
        if todo:
            continue
        done_file = handoff / "done"
        finished = done_file.exists() and done_file.stat().st_mtime >= started
        if finished or not pid_alive(solver_pid):
            write_summary(handoff, finished, n_decisions)      # completed: the solver reached its end()
            return n_decisions
        if max_idle_s is not None and time.time() - idle_since > max_idle_s:
            return n_decisions
        if idle_timeout_s is not None and last_state is not None and time.time() - last_state > idle_timeout_s:
            print(f"jev-governor: no new state for {idle_timeout_s:.0f} s, giving up", flush=True)
            write_summary(handoff, False, n_decisions)
            return n_decisions
        time.sleep(poll_s)
