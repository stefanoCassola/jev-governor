"""Summarize simpleFoamMod runs: iterations, execution time, final permeability, relaxation path.

usage: summarize_runs.py <run_dir> [<run_dir> ...] [--ref <run_dir>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamlog import parse_full  # noqa: E402


def summary(run: Path) -> dict:
    L = parse_full(run / "log.simpleFoamMod")
    last = L.iters[-1] if L.iters else None
    d = {"run": run.name, "converged": L.converged, "fatal": L.fatal,
         "iterations": last.it if last else 0,
         "exec_s": last.exec_s if last else None, "clock_s": last.clock_s if last else None,
         "perm": last.perm if last else None, "slope": last.slope if last else None,
         "relax_changes": L.rereads, "restarts": len(L.restarts)}
    # Supervised runs: ExecutionTime restarts at 0 in every segment, so sum over segments
    # (this includes the work lost to crashes; exec_s_clean excludes it).
    segf = run / "segments.json"
    if segf.exists() and last:
        segs = json.loads(segf.read_text())
        finished = sum(s["exec_s"] or 0 for s in segs)
        running = 0 if (run / "supervisor_done").exists() else (last.exec_s or 0)
        d["exec_s"] = round(finished + running, 1)
        lost_it = sum(max(0, (s["last_iteration"] or 0) - (segs[i + 1]["restart_from"] if i + 1 < len(segs) else s["last_iteration"] or 0))
                      for i, s in enumerate(segs))
        d["lost_iterations"] = lost_it
        done_it = d["iterations"] + lost_it
        d["exec_s_clean"] = round(d["exec_s"] * d["iterations"] / done_it, 1) if done_it else None
    for f in ("start_epoch", "end_epoch"):
        if (run / f).exists():
            d[f] = float((run / f).read_text())
    if "start_epoch" in d and "end_epoch" in d:
        d["wall_s"] = round(d["end_epoch"] - d["start_epoch"], 1)
    dec = run / "jev_decisions.jsonl"
    if dec.exists():
        recs = [json.loads(x) for x in dec.read_text().splitlines() if x.strip()]
        ds = [r for r in recs if r.get("kind") == "decision"]
        d["jev_calls"] = len(ds)
        d["jev_latency_s_total"] = round(sum(r["latency_s"] for r in ds), 1)
        d["guards"] = sum(1 for r in recs if r.get("kind") == "guard")
        d["relax_path"] = [(r["iteration"], r["to"][0], r["to"][1]) for r in recs
                           if r.get("kind") in ("decision", "guard", "restart") and "to" in r and r["to"] != r["from"]]
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--ref", type=Path)
    ap.add_argument("--ref-perm", type=float, help="reference permeability (e.g. cluster result)")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rows = [summary(r) for r in a.runs]
    ref = summary(a.ref) if a.ref else None
    kref = a.ref_perm or (ref["perm"] if ref else None)
    print(f"{'run':28s} {'conv':>5s} {'iters':>6s} {'exec_s':>9s} {'wall_s':>9s} {'K [m2]':>12s} "
          f"{'dK vs ref':>9s} {'it ratio':>8s} {'t ratio':>8s} {'restarts':>8s}")
    for d in rows:
        dk = (d["perm"] / kref - 1) if (kref and d["perm"]) else None
        itr = d["iterations"] / ref["iterations"] if ref else None
        tr = (d.get("exec_s_clean") or d["exec_s"]) / (ref.get("exec_s_clean") or ref["exec_s"]) \
            if ref and d["exec_s"] and ref["exec_s"] else None
        d.update(dK_vs_ref=dk, iter_ratio=itr, exec_ratio=tr)
        print(f"{d['run']:28s} {str(d['converged']):>5s} {d['iterations']:6d} {d['exec_s'] or 0:9.1f} "
              f"{d.get('wall_s', 0):9.1f} {d['perm'] or 0:12.5e} "
              f"{(f'{dk*100:+.2f}%' if dk is not None else '-'):>9s} "
              f"{(f'{itr:.3f}' if itr else '-'):>8s} {(f'{tr:.3f}' if tr else '-'):>8s} {d['restarts']:8d}")
        if d.get("relax_path"):
            print("    relax path (it, U, p):", d["relax_path"])
    if a.json:
        a.json.write_text(json.dumps({"ref": ref, "ref_perm": kref, "runs": rows}, indent=1))


if __name__ == "__main__":
    main()
