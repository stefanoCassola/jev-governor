"""Run a list of relaxctl tasks on one node with a fixed number of concurrent workers.

tasks file: one JSON object per line: {"template": "...", "run": "...", "args": [...]}
Runs whose result.json already exists are skipped (so a batch can be resubmitted).
usage: batch.py <tasks.jsonl> [--workers 20]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent


LOCAL = Path(os.environ.get("TMPDIR", "/tmp")) / f"oj_{os.environ.get('SLURM_JOB_ID', 'x')}"


def run(t: dict) -> str:
    """Run on node-local disk (fast sync hand-off), then move the run dir to bench/runs."""
    rd = BENCH / "runs" / t["run"]
    if (rd / "result.json").exists():
        return f"skip {t['run']}"
    rd.parent.mkdir(parents=True, exist_ok=True)
    loc = LOCAL / t["run"]
    loc.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(HERE / t.get("script", "pimplectl.py")),
           str(BENCH / t.get("cases", "cases_t") / t["template"]), str(loc), *t["args"]]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if rd.exists():
        shutil.rmtree(rd)
    if loc.exists():
        shutil.rmtree(loc / "dynamicCode", ignore_errors=True)
        shutil.copytree(loc, rd, symlinks=True)
        shutil.rmtree(loc, ignore_errors=True)
    if p.returncode != 0:
        (rd.parent / f"{rd.name}.err").write_text(p.stdout[-4000:] + "\n" + p.stderr[-4000:])
        return f"FAIL {t['run']}"
    return f"done {t['run']}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", type=Path)
    ap.add_argument("--workers", type=int, default=20)
    a = ap.parse_args()
    tasks = [json.loads(x) for x in a.tasks.read_text().splitlines() if x.strip()]
    with ThreadPoolExecutor(a.workers) as ex:
        for msg in ex.map(run, tasks):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
