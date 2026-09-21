# How often did an async decision see a window without >=5 settled iterations after the previous change was applied?
import json, glob, collections
SETTLE = 5
agg = collections.defaultdict(lambda: [0, 0, 0, 0])  # changes, stale next decisions, stale & same-direction change, runs
for g in ("v4", "v4held2"):
    for d in glob.glob(f"/scratch/cassola/open_jev/bench/runs/{g}/*__jev4_async_r*"):
        try:
            R = json.load(open(d + "/result.json")); D = [json.loads(l) for l in open(d + "/decisions.jsonl")]
        except Exception:
            continue
        app = sorted(R.get("async_applied_at") or [])
        fam = d.split("/")[-1].split("__")[0].split("_")[0] + ("_" + d.split("/")[-1].split("_")[1] if d.split("/")[-1].startswith("pm") else "")
        fam = d.split("/")[-1].split("__")[0].rsplit("_", 1)[0]
        a = agg[fam]; a[3] += 1
        for i, x in enumerate(D[:-1]):
            if x["to"]["U"] == x["from"]["U"]:
                continue
            a[0] += 1
            ap = next((t for t in app if t >= x["iteration"]), None)
            nxt = D[i + 1]
            if ap is None or nxt["iteration"] < ap + SETTLE:
                a[1] += 1
                s1 = x["to"]["U"] - x["from"]["U"]; s2 = nxt["to"]["U"] - nxt["from"]["U"]
                if s1 * s2 > 0:
                    a[2] += 1
for k, (c, s, same, n) in sorted(agg.items()):
    print(f"{k:12s} runs={n:2d} changes={c:4d} next-decision-stale={s:4d} ({100*s/max(c,1):.0f}%) stale+same-dir={same:4d}")
