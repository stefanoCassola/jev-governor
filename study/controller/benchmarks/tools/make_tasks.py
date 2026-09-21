"""Generate the benchmark parameter-study task list.

usage: make_tasks.py <cases...> --group main --out tasks_main.jsonl
Run names: <group>/<template>__<variant>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
CAP = {"cavity": 20000, "lbfs": 20000, "pitzDaily": 5000, "pm": 4000}
GRID = {"SIMPLE": ([0.5, 0.6, 0.7, 0.8, 0.9], [0.1, 0.2, 0.3, 0.5, 0.7]),
        "SIMPLEC": ([0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.98], [0.3, 0.5, 0.7, 0.9, 1.0])}
LOW = {"SIMPLE": ["0.5", "0.2"], "SIMPLEC": ["0.5", "0.3"]}


def tasks_for(case: str, group: str, reps: int) -> list[dict]:
    prof = json.loads((BENCH / "cases" / case / "case.json").read_text())
    base = case.split("_")[0]
    cap = str(CAP[base])
    algo = prof["algorithm"]
    out = []

    def add(variant, *args):
        out.append({"template": case, "run": f"{group}/{case}__{variant}", "args": [*args, "--end", cap]})

    add("default", "--method", "static")
    us, ps = GRID[algo]
    for u in us:
        for p in ps:
            add(f"grid_U{u}_p{p}", "--method", "static", "--relax", str(u), str(p))
    add("heuristic", "--method", "heuristic", "--interval", "10")
    add("heuristic_low", "--method", "heuristic", "--interval", "10", "--relax", *LOW[algo])
    for r in range(reps):
        add(f"jev_r{r}", "--method", "jev", "--interval", "10")
    for n in (5, 20, 50):
        add(f"jev_i{n}", "--method", "jev", "--interval", str(n))
    add("jev_s0.5", "--method", "jev", "--interval", "10", "--scale", "0.5")
    add("jev_low", "--method", "jev", "--interval", "10", "--relax", *LOW[algo])
    add("jev_nounst", "--method", "jev", "--interval", "10", "--no-unstable")
    if base == "pm":   # Pawar & Maulik action space [0.2, 0.95] and cadence 100
        pmb = ["--bounds", "0.2", "0.95", "0.2", "0.95"]
        add("jev_pmbounds_i10", "--method", "jev", "--interval", "10", *pmb)
        add("jev_pmbounds_i100", "--method", "jev", "--interval", "100", *pmb)
        add("heuristic_pmbounds", "--method", "heuristic", "--interval", "10", *pmb)
    # tight reference for the field error (default factors, targets x1e-3, 4x cap)
    out.append({"template": case, "run": f"ref/{case}__ref",
                "args": ["--method", "static", "--tight", "1e-3", "--end", str(4 * int(cap))]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--group", default="main")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    ts = [t for c in a.cases for t in tasks_for(c, a.group, a.reps)]
    # long runs first so the pool drains evenly: adaptive and reference runs first
    ts.sort(key=lambda t: (not t["run"].startswith("ref/"), "grid" in t["run"]))
    a.out.write_text("".join(json.dumps(t) + "\n" for t in ts))
    print(len(ts), "tasks ->", a.out)


if __name__ == "__main__":
    main()
