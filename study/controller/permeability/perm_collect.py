"""Collect all permeability runs (default, fixed 0.95/1.0, 0.98/1.0, 0.99/1.0, Jev round 1, Jev round 2) into one CSV.

usage: perm_collect.py <runs_dir> <out.csv>
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_runs import summary  # noqa: E402

CASES = ["base", "med_x", "med_y", "med_z", "p90_x", "fvc56_x", "long_y"]
# run directory names per case and method; the base case predates the fg_ naming
NAMES = {"default": "{p}_{m}", "s_U95_p10": "{p}_s_U95_p10", "s_U98_p10": "{p}_s_U98_p10", "s_U99_p10": "{p}_s_U99_p10",
         "jev1": "{p}_jev", "jev2": "{p}_jevrr"}


def run_name(case: str, method: str) -> str:
    if case == "base":
        return {"default": "base_ref", "s_U95_p10": "base_s_U95_p10", "s_U98_p10": "base_s_U98_p10",
                "s_U99_p10": "base_s_U99_p10", "jev1": "base_jev1", "jev2": "base_jevrr"}[method]
    return NAMES[method].format(p=f"fg_{case}", m="default")


def main() -> None:
    runs, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for c in CASES:
        for m in NAMES:
            d = runs / run_name(c, m)
            if not (d / "log.simpleFoamMod").exists():
                continue
            s = summary(d)
            # ClockTime at the end of iteration 1: start-up incl. mesh reading, which varies with
            # concurrent file-system load (up to an hour with three 17M-cell meshes read at once).
            first = None
            with open(d / "log.simpleFoamMod", errors="ignore") as fh:
                for ln in fh:
                    if "ClockTime" in ln:
                        first = float(ln.split("ClockTime =")[1].split()[0])
                        break
            s["startup_s"] = first
            s["run_s"] = (s["clock_s"] - first) if (first is not None and s.get("clock_s")) else None
            s.update(case=c, method=m, finished=(d / "end_epoch").exists(),
                     diverged=(d / "CANCELLED_DIVERGED").exists())   # run cancelled by hand after divergence
            rows.append(s)
    keys = ["case", "method", "run", "finished", "diverged", "converged", "fatal", "iterations", "exec_s",
            "wall_s", "startup_s", "run_s", "clock_s", "perm", "start_epoch", "end_epoch"]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
