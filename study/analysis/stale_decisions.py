"""How often did an asynchronous decision follow a factor change before five settled iterations with the new
factors existed?  usage: stale_decisions.py <records_dir> <results_dir>
<records_dir>: extracted data/decision_records.tar.gz (decisions.jsonl and result.json of every run)."""
import collections
import glob
import json
import sys
from pathlib import Path

from common import write_numbers

SETTLE = 5
root, res = Path(sys.argv[1]), Path(sys.argv[2])
out = {}
for arm, groups, pat in (("ctl_main", ("v4", "v4held2"), "*__jev4_async_r*"), ("ctl_same_batch", ("v4a", "v4aheld2"), "*__jev4_async_r*"),
                         ("ack", ("v4a", "v4aheld2"), "*__jev4ack_async_r*")):
    agg = collections.defaultdict(lambda: [0, 0, 0])
    for g in groups:
        for d in glob.glob(str(root / g / pat)):
            R = json.load(open(d + "/result.json")); D = [json.loads(x) for x in open(d + "/decisions.jsonl") if x.strip()]
            app = sorted(R.get("async_applied_at") or [])
            fam = Path(d).name.split("__")[0].rsplit("_", 1)[0]
            for i, x in enumerate(D[:-1]):
                if x["to"]["U"] == x["from"]["U"]:
                    continue
                a = agg[fam]; a[0] += 1
                ap = next((t for t in app if t >= x["iteration"]), None)
                nxt = D[i + 1]
                if ap is None or nxt["iteration"] < ap + SETTLE:
                    a[1] += 1
                    if (x["to"]["U"] - x["from"]["U"]) * (nxt["to"]["U"] - nxt["from"]["U"]) > 0:
                        a[2] += 1
    out[arm] = {k: {"changes": c, "stale_pct": 100 * s / max(c, 1), "compound_pct": 100 * m / max(c, 1)} for k, (c, s, m) in agg.items()}
    print(arm, {k: (v["changes"], round(v["stale_pct"]), round(v["compound_pct"])) for k, v in sorted(out[arm].items())})
fams = ("cavity", "pitzDaily", "pm_bfs")
M = {"StaleMainLo": min(out["ctl_main"][f]["stale_pct"] for f in fams), "StaleMainHi": max(out["ctl_main"][f]["stale_pct"] for f in fams),
     "StaleCtlLo": min(out["ctl_same_batch"][f]["stale_pct"] for f in fams), "StaleCtlHi": max(out["ctl_same_batch"][f]["stale_pct"] for f in fams),
     "StaleAckHi": max(out["ack"][f]["stale_pct"] for f in fams if f in out["ack"]),
     "CompoundLo": min(out["ctl_main"][f]["compound_pct"] for f in fams), "CompoundHi": max(out["ctl_main"][f]["compound_pct"] for f in fams)}
write_numbers(res / "numbers_stale.csv", {k: f"{v:.0f}" for k, v in M.items()}, "stale_decisions.py")
print(M)
