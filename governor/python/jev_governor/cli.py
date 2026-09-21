"""Command line: jev-governor serve | decide | report | check."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .judges import make_judge
from .policy import Governor
from .sidecar import serve


def cmd_serve(a) -> int:
    n = serve(Path(a.case), backend=a.backend, pid=a.pid, model=a.model, idle_timeout_s=a.idle_timeout)
    print(f"jev-governor: {n} decisions, solver finished")
    return 0


def cmd_decide(a) -> int:
    """One-off decisions for state files, in order (debugging, replaying a run with another judge)."""
    gov = None
    for f in a.state:
        state = json.loads(Path(f).read_text())
        if gov is None:
            cfg = state["config"]
            gov = Governor(make_judge(a.backend or cfg.get("backend", "rules"), cfg.get("model", "jev-1.13.0")))
        d = gov.decide(state)
        print(f"# {f}\n{d.to_foam()}")
        if a.verbose:
            print(json.dumps(d.info, indent=2))
    return 0


def cmd_report(a) -> int:
    path = Path(a.case) / "jevGovernor" / "decisions.jsonl"
    if not path.exists():
        print(f"no decisions logged in {path}", file=sys.stderr)
        return 1
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    moves: dict[str, int] = {}
    for r in recs:
        moves[r["move"]] = moves.get(r["move"], 0) + 1
    lat = [r["latency_s"] for r in recs if "latency_s" in r]
    print(f"decisions        {len(recs)}  (judge: {recs[-1].get('judge', '?')})")
    print("moves            " + ", ".join(f"{k}={v}" for k, v in sorted(moves.items())))
    print(f"guards           {sum(1 for r in recs if 'guard' in r)}")
    if lat:
        print(f"judge latency    mean {sum(lat) / len(lat):.3f} s, max {max(lat):.3f} s, total {sum(lat):.1f} s")
    print(f"API retries      {sum(r.get('api_retries', 0) for r in recs)}")
    from .sidecar import steps_summary
    t = steps_summary(Path(a.case) / "jevGovernor")
    if t:                                                    # PIMPLE mode
        print(f"time steps       {t['steps']}, {t['outer_total']} outer iterations "
              f"({t['outer_total'] / t['steps']:.1f} per step)")
        print(f"capped steps     {t['slow_capped_steps']} slow, {t['unstable_steps']} unstable "
              f"({100 * (t['slow_capped_steps'] + t['unstable_steps']) / t['steps']:.1f} % of all steps)")
        print(f"final factors    U {t['final_factors']['U']}, p {t['final_factors']['p']}")
        events = [e for r in recs for e in r.get("guard_events", [])]
        print(f"guard actions    {len(events)}")
    print(f"upwind at end    {recs[-1]['upwind']}")
    if recs[-1]["upwind"]:
        print("WARNING: the run ended on first-order upwind convection schemes.")
    if a.verbose:
        for r in recs:
            print(f"  {r['iteration']:>7}  {r['note']}")
    return 0


def cmd_check(a) -> int:
    """Send one harmless request to Jev to verify key, connectivity and latency."""
    import time
    from .features import FieldFeatures, Features
    from .judges import BackendUnavailable
    try:
        judge = make_judge("jev", a.model)
    except BackendUnavailable as ex:
        print(f"jev-governor: {ex}", file=sys.stderr)
        return 2
    feats = Features(window=(1, 50), fields={"p": FieldFeatures(s100=-0.6, orders=2.0)}, slowest="p", bad=False)
    state = {"solver": "connectivity check", "momentum_factor": 0.7,
             "residuals": {"slowest_equation": "p: falling steadily, smooth, one to two orders of magnitude "
                                               "above its target", "other_equations": ["U: falling steadily, smooth"]},
             "observations": ["the momentum under-relaxation factor has not been changed yet"]}
    t0 = time.time()
    try:
        probs, info = judge.judge(feats, state)
    except BackendUnavailable as ex:
        print(f"jev-governor: {ex}", file=sys.stderr)
        return 2
    print(json.dumps({"probs": probs, **info, "wall_s": round(time.time() - t0, 3)}, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jev-governor", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="answer the state files of a running solver")
    s.add_argument("--case", default=".")
    s.add_argument("--backend", choices=["rules", "jev", "mock"], help="override the function object setting")
    s.add_argument("--model", help="Jev model version (default: the function object setting)")
    s.add_argument("--pid", type=int, help="solver PID; the sidecar exits when it disappears (default: the PID "
                                           "in the state file, if it is visible from here)")
    s.add_argument("--idle-timeout", type=float, metavar="S",
                   help="exit when no new state has arrived for S seconds after the first one (batch safety net)")
    s.set_defaults(fn=cmd_serve)

    d = sub.add_parser("decide", help="decide for state files offline")
    d.add_argument("state", nargs="+")
    d.add_argument("--backend", choices=["rules", "jev", "mock"])
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(fn=cmd_decide)

    r = sub.add_parser("report", help="summarise the decisions of a run")
    r.add_argument("--case", default=".")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_report)

    c = sub.add_parser("check", help="verify the TypeSafe API key and measure latency")
    c.add_argument("--model", default="jev-1.13.0")
    c.set_defaults(fn=cmd_check)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
