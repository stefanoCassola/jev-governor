"""Markdown table of a benchmark directory."""
import math
import re
import sys
from pathlib import Path

work, backend = Path(sys.argv[1]), sys.argv[2]
TARGETS = {"pitzDaily": {"Ux": 1e-3, "Uy": 1e-3, "p": 1e-2, "k": 1e-3, "epsilon": 1e-3},
           "airFoil2D": {"Ux": 1e-5, "Uy": 1e-5, "p": 1e-5, "nuTilda": 1e-5}}
RES = re.compile(r"Solving for (\w+), Initial residual = ([\d.e+-]+)")


def result(run: Path, case: str) -> tuple[str, str]:
    log = (run / "log.simpleFoam").read_text(errors="replace")
    wall = (run / "wall.txt").read_text().split()[-1] if (run / "wall.txt").exists() else "?"
    m = re.search(r"converged in (\d+) iterations", log)
    if m:
        return f"{m.group(1)} iterations", wall
    if "FOAM FATAL" in log or "sigHandler" in log or "printStack" in log:
        return "crashed", wall
    tail = log[-60000:]                                      # roughly the last 50 iterations
    worst = {}
    for name, val in RES.findall(tail):
        if name in TARGETS[case] and float(val) > 0:
            worst.setdefault(name, []).append(math.log10(float(val) / TARGETS[case][name]))
    level = max(sum(v) / len(v) for v in worst.values())
    return f"not converged, {level:.2f} decades above target", wall


print(f"| case | start factor | fixed | governed ({backend}) | wall fixed / governed [s] |")
print("|---|---|---|---|---|")
for case in TARGETS:
    for run in sorted(work.glob(f"{case}_fixed_*")):
        f = run.name.split("_")[-1]
        a, wa = result(run, case)
        b, wb = result(work / f"{case}_gov_{f}", case)
        print(f"| {case} | {f} | {a} | {b} | {wa} / {wb} |")
