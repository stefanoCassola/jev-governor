"""Print the decision path of one run: showdec.py <run_dir>"""
import json, sys
for l in open(sys.argv[1] + "/decisions.jsonl"):
    r = json.loads(l); f = r["features"]["fields"]
    fs = "; ".join(f"{k}:{v['trend']},{v['oscillation']},{v['orders_to_target']}" for k, v in f.items())
    print(r["iteration"], r["from"]["U"], r["from"]["p"], "->", r["to"]["U"], r["to"]["p"], r["u_move"], r["p_move"],
          r.get("raw"), "unst=%.2f" % r.get("unstable", 0), "|", fs)
