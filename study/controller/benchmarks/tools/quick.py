"""Print a one-line summary per run: quick.py <glob>"""
import glob, json, sys
for f in sorted(glob.glob(sys.argv[1] + "/result.json")):
    r = json.load(open(f)); ff = r["final_factors"]
    print(f"{r['run']:34s} conv={r['converged']!s:5s} fatal={r['fatal']!s:5s} it={r['iterations']:6d} "
          f"exec={r['exec_s']} wait={r['controller_wait_s']} dec={r['decisions']} U={ff['U']} p={ff['p']}")
