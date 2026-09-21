"""v4a study: async with acknowledged changes (jev4ack_async) vs a same-batch v4 async control and default.
Groups: v4a (design set + held-out 1 conditions), v4aheld2 (held-out 2). Laminar step: 1 repeat per arm."""
import json
DESIGN = ["cavity_Re100", "cavity_Re400", "cavity_Re1000", "lbfs_Re400", "lbfs_Re800", "pitzDaily",
          "pm_bfs_U25", "pm_bfs_U44", "pm_bfs_U50", "pm_bfs_U75"]
HELD = ["cavity_Re700", "lbfs_Re300", "lbfs_Re600", "pitzDaily_U20", "pm_bfs_U35", "pm_bfs_U65"]
HELD2 = ["cavity_Re250", "lbfs_Re500", "pitzDaily_U15", "pm_bfs_U30", "pm_bfs_U55", "pm_bfs_U70"]
def end(c): return "4000" if c.startswith("pm") else ("5000" if c.startswith("pitz") else "20000")
tasks = []
for c in DESIGN + HELD + HELD2:
    g = "v4aheld2" if c in HELD2 else "v4a"
    reps = 1 if c.startswith("lbfs") else 3
    t = lambda name, args: {"template": c, "cases": "cases_async", "run": f"{g}/{c}__{name}",
                            "args": [*args, "--end", end(c)]}
    tasks.append(t("default", ["--method", "static"]))
    for r in range(reps):
        tasks.append(t(f"jev4ack_async_r{r}", ["--method", "jev4", "--interval", "10", "--async", "--ack"]))
        tasks.append(t(f"jev4_async_r{r}", ["--method", "jev4", "--interval", "10", "--async"]))
# longest first per batch (laminar step), round-robin over 5 batches
tasks.sort(key=lambda x: 0 if x["template"].startswith("lbfs") else 1)
for b in range(5):
    with open(f"tasks_v4a_{b}.jsonl", "w") as f:
        for x in tasks[b::5]:
            f.write(json.dumps(x) + "\n")
print(len(tasks), "tasks")
