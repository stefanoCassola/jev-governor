"""Collect result.json of all runs under bench/runs/<group>/ into one CSV, with the relative L2
error of the final U field against the reference run of the same template.

Reference run convention: runs/ref/<template>__ref (default factors, targets x1e-3).
usage: collect.py <group> [<group> ...] --out results.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parents[1]
_cache: dict = {}


def final_dir(run: Path) -> Path | None:
    ts = [p for p in run.iterdir() if p.is_dir() and re.fullmatch(r"\d+(\.\d+)?", p.name) and p.name != "0"]
    return max(ts, key=lambda p: float(p.name)) if ts else None


def read_U(run: Path) -> np.ndarray | None:
    d = final_dir(run)
    if d is None or not (d / "U").exists():
        return None
    s = (d / "U").read_text()
    m = re.search(r"internalField\s+nonuniform\s+List<vector>\s*(\d+)\s*\(", s)
    if not m:
        return None
    n = int(m.group(1))
    body = s[m.end():]
    nums = np.array(re.findall(r"\(([^()]*)\)", body[: body.find("\n)\n") + 1] if "\n)\n" in body else body)[:n],
                    dtype=object)
    arr = np.array([[float(x) for x in t.split()] for t in nums])
    return arr if len(arr) == n else None


def ref_U(template: str):
    if template not in _cache:
        r = BENCH / "runs/ref" / f"{template}__ref"
        _cache[template] = read_U(r) if r.exists() else None
    return _cache[template]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("groups", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = []
    for g in a.groups:
        for rj in sorted((BENCH / "runs" / g).glob("*/result.json")):
            r = json.loads(rj.read_text())
            run = rj.parent
            r["group"] = g
            u, ur = read_U(run), ref_U(r["template"])
            r["u_err_l2"] = (float(np.linalg.norm(u - ur) / np.linalg.norm(ur))
                             if u is not None and ur is not None and u.shape == ur.shape else None)
            dec = run / "decisions.jsonl"
            if dec.exists():
                recs = [json.loads(x) for x in dec.read_text().splitlines() if x.strip()]
                lat = [x.get("latency_s", 0) for x in recs if "latency_s" in x]
                r["api_latency_s"] = round(sum(lat), 2)
                r["n_changes"] = sum(1 for x in recs if x["to"]["U"] != x["from"]["U"] or x["to"]["p"] != x["from"]["p"])
                r["path"] = ";".join(f"{x['iteration']}:{x['to']['U']}/{x['to']['p']}" for x in recs
                                     if x["to"]["U"] != x["from"]["U"] or x["to"]["p"] != x["from"]["p"])
                r["errors"] = sum(1 for x in recs if "error" in x)
            ff = r.pop("final_factors", {}) or {}
            r["final_U"], r["final_p"] = ff.get("U"), ff.get("p")
            r["init"] = " ".join(map(str, r["init"])) if r.get("init") else ""
            r.pop("final_res", None)
            rows.append(r)
    keys = sorted({k for r in rows for k in r})
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {a.out}")


if __name__ == "__main__":
    main()
