"""Shared loading and case metadata for the analysis scripts."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DESIGN = ["cavity_Re100", "cavity_Re400", "cavity_Re1000", "lbfs_Re400", "lbfs_Re800", "pitzDaily",
          "pm_bfs_U25", "pm_bfs_U44", "pm_bfs_U50", "pm_bfs_U75"]
HELD1 = ["cavity_Re700", "lbfs_Re300", "lbfs_Re600", "pitzDaily_U20", "pm_bfs_U35", "pm_bfs_U65"]
HELD2 = ["cavity_Re250", "lbfs_Re500", "pitzDaily_U15", "pm_bfs_U30", "pm_bfs_U55", "pm_bfs_U70"]
ALL = DESIGN + HELD1 + HELD2
FAMILIES = {"cavity": "Cavity", "lbfs": "Laminar step", "pitzDaily": "pitzDaily", "pm_bfs": "P--M step"}
LABEL = {"cavity_Re100": "Cavity $Re$ 100", "cavity_Re250": "Cavity $Re$ 250", "cavity_Re400": "Cavity $Re$ 400",
         "cavity_Re700": "Cavity $Re$ 700", "cavity_Re1000": "Cavity $Re$ 1000",
         "lbfs_Re300": "Lam. step $Re$ 300", "lbfs_Re400": "Lam. step $Re$ 400", "lbfs_Re500": "Lam. step $Re$ 500",
         "lbfs_Re600": "Lam. step $Re$ 600", "lbfs_Re800": "Lam. step $Re$ 800",
         "pitzDaily": "pitzDaily 10 m/s", "pitzDaily_U15": "pitzDaily 15 m/s", "pitzDaily_U20": "pitzDaily 20 m/s",
         "pm_bfs_U25": "P--M step 25 m/s", "pm_bfs_U30": "P--M step 30 m/s", "pm_bfs_U35": "P--M step 35 m/s",
         "pm_bfs_U44": "P--M step 44.2 m/s", "pm_bfs_U50": "P--M step 50 m/s", "pm_bfs_U55": "P--M step 55 m/s",
         "pm_bfs_U65": "P--M step 65 m/s", "pm_bfs_U70": "P--M step 70 m/s", "pm_bfs_U75": "P--M step 75 m/s"}


def family(case: str) -> str:
    return next(f for f in FAMILIES if case.startswith(f))


def set_name(case: str) -> str:
    return "D" if case in DESIGN else ("H1" if case in HELD1 else "H2")


def sort_key(case: str):
    import re
    return (list(FAMILIES).index(family(case)), float(re.search(r"(\d+)$", case).group(1)) if re.search(r"\d+$", case) else 10)


def load(data: Path) -> pd.DataFrame:
    d = pd.concat([pd.read_csv(data / "results_all.csv"), pd.read_csv(data / "results_v4a.csv")], ignore_index=True)
    d["variant"] = d["run"].str.split("__").str[1]
    d["kind"] = d["variant"].str.replace(r"_r\d+$", "", regex=True)
    d["ok"] = d["converged"].astype(bool) & ~d["fatal"].astype(bool)
    d["spi"] = d["exec_s"] / d["iterations"]                       # solver seconds per iteration
    d["other_s"] = d["wall_s"] - d["exec_s"] - d["controller_wait_s"].fillna(0)   # start-up etc.
    return d


def gmean(x) -> float:
    x = np.asarray([v for v in x if v is not None and np.isfinite(v) and v > 0], float)
    return float(np.exp(np.log(x).mean())) if len(x) else float("nan")


# ------------------------------------------------------------------ output writers (Markdown tables, CSV numbers)
import re as _re

_SUBS = [("{,}", ","), (r"\approx", "≈"), (r"\alpha_U", "α_U"), (r"\alpha_p", "α_p"), (r"\aU", "α_U"), (r"\ap", "α_p"), (r"\Delta", "Δ"),
         (r"\times", "×"), (r"^\dagger", "†"), (r"^\circ", "°"), ("^*", "*"), (r"\%", "%"),
         (r"\ ", " "), (r"\&", "&"), (r"\,", ""), ("$-$", "−"), ("10^6", "10⁶"), (r"\varE", "variant E"), (r"\varP", "variant P"),
         (r"\citet{Pawar2021}", "Pawar & Maulik (2021)"), (r"\citep{Pawar2021}", "(Pawar & Maulik 2021)"),
         ("--", "–"), ("~", " "), ("$", "")]


def _clean(cell: str) -> str:
    for a, b in _SUBS:
        cell = cell.replace(a, b)
    cell = _re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", cell)
    cell = _re.sub(r"\\[a-zA-Z]+", "", cell)
    return cell.replace("|", "\\|").strip()


def _split(row: str):
    return [c.strip() for c in _re.split(r"(?<!\\)&", row.strip().removesuffix(r"\\"))]


def _cells(row: str):
    out = []
    for c in _split(row):
        m = _re.match(r"\s*\\multicolumn\{(\d+)\}\{[^}]*\}\{(.*)\}\s*$", c)
        out += [_clean(m.group(2))] + [""] * (int(m.group(1)) - 1) if m else [_clean(c)]
    return out


def write_table(path, lines) -> None:
    """Write a table built as tabular rows ('a & b \\\\') as a Markdown table; a grouping header row
    (column spans) is merged into the column names."""
    skip = ("\\begin", "\\end", "\\toprule", "\\midrule", "\\bottomrule", "\\cmidrule", "\\addlinespace")
    rows = [l for l in lines if not l.strip().startswith(skip)]
    groups, header, body = None, None, []
    for l in rows:
        if header is None and "\\multicolumn" in l and groups is None and not body:
            groups = []
            for c in _split(l):
                m = _re.match(r"\s*\\multicolumn\{(\d+)\}\{[^}]*\}\{(.*)\}\s*$", c)
                groups += [_clean(m.group(2))] * int(m.group(1)) if m else [_clean(c)]
        elif header is None:
            header = _cells(l)
        else:
            body.append(_cells(l))
    if groups:
        header = [f"{g}: {h}" if g else h for g, h in zip(groups, header)]
    n = len(header)
    md = ["| " + " | ".join(header) + " |", "|" + "---|" * n]
    md += ["| " + " | ".join((r + [""] * n)[:n]) + " |" for r in body]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(md) + "\n")


def write_numbers(path, M: dict, source: str) -> None:
    """Write the named numbers of one analysis script as CSV (name,value)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# generated by analysis/{source} -- do not edit\nname,value\n" +
                    "".join(f"{k},\"{_clean(str(v))}\"\n" for k, v in sorted(M.items())))
