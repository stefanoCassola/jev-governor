"""Run simpleFoamMod serially with automatic restart from the latest checkpoint after a crash.

This machine shows sporadic non-deterministic floating-point faults under load (the same
state solved twice gives a finite result once and a blow-up the other time), so every run,
reference and Jev-controlled alike, is supervised the same way: the case writes a checkpoint
every `writeInterval` iterations (purgeWrite 2, startFrom latestTime) and a crashed segment
is restarted from the newest checkpoint. All segments append to one log.simpleFoamMod, each
preceded by a marker line that LogReader understands.

Writes segments.json (per-segment timing) and, when finished, supervisor_done.

usage: supervise.py <run_dir> <cpu> [--max-restarts 30]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

SIF = Path.home() / "openfoam2312Mod.sif"
MARK = "=== supervisor: segment {k} start, restart from time {t} ==="
RE_EXEC = re.compile(r"^ExecutionTime = ([\d.]+) s")
RE_TIME = re.compile(r"^Time = (\d+)$")


def latest_time(case: Path) -> int:
    """Newest checkpoint that looks completely written."""
    def ok(d: Path) -> bool:
        return d.name == "0" or all((d / f).is_file() and (d / f).stat().st_size > 0
                                    for f in ("U", "p", "phi", "uniform/time"))
    ts = [int(p.name) for p in case.iterdir() if p.is_dir() and p.name.isdigit() and ok(p)]
    return max(ts, default=0)


def segment_tail(log: Path, offset: int) -> dict:
    """Outcome, last iteration and last ExecutionTime of the segment starting at byte `offset`."""
    with open(log, "r", errors="replace") as f:
        f.seek(offset)
        txt = f.read()
    last_exec, last_it = None, None
    for ln in txt.splitlines():
        if (m := RE_EXEC.match(ln)):
            last_exec = float(m.group(1))
        elif (m := RE_TIME.match(ln)):
            last_it = int(m.group(1))
    converged = "Convergence is reached" in txt
    ended = any(ln.startswith("End") for ln in txt.splitlines())
    return {"converged": converged, "ended": ended, "last_iteration": last_it, "exec_s": last_exec}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("cpu", type=int)
    ap.add_argument("--max-restarts", type=int, default=30)
    a = ap.parse_args()
    case = a.case.resolve()
    log = case / "log.simpleFoamMod"
    segs: list[dict] = []
    for k in range(a.max_restarts + 1):
        t0 = latest_time(case)
        with open(log, "a") as f:
            f.write(("\n" if k else "") + MARK.format(k=k, t=t0) + "\n")
            offset = f.tell()
        start = time.time()
        with open(log, "a") as f:
            rc = subprocess.run(
                ["taskset", "-c", str(a.cpu), "stdbuf", "-oL", "-eL", "apptainer", "exec", "--bind", "/media",
                 str(SIF), "openfoam2312", "simpleFoamMod"],
                cwd=case, stdout=f, stderr=subprocess.STDOUT).returncode
        seg = {"segment": k, "restart_from": t0, "start_epoch": start, "end_epoch": time.time(),
               "returncode": rc, **segment_tail(log, offset)}
        segs.append(seg)
        (case / "segments.json").write_text(json.dumps(segs, indent=1))
        if seg["converged"] or (seg["ended"] and rc == 0):
            break
        # A segment that died before completing a single iteration suggests a bad checkpoint.
        if t0 > 0 and (seg["last_iteration"] is None or seg["last_iteration"] <= t0 + 1):
            if len(segs) >= 2 and segs[-2]["restart_from"] == t0:
                subprocess.run(["rm", "-rf", str(case / str(t0))])
        time.sleep(2)
    (case / "supervisor_done").write_text(json.dumps(segs[-1]))


if __name__ == "__main__":
    main()
