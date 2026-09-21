"""Independent re-derivation of iterations/converged from raw solver logs (no project code used)."""
import json, re, subprocess, sys
from pathlib import Path
root = Path('/scratch/cassola/open_jev/bench/runs')
groups = sys.argv[1:]
bad = 0; n = 0
for g in groups:
    for d in sorted((root / g).iterdir()):
        log = d / 'log.simpleFoam'; rj = d / 'result.json'
        if not log.exists() or not rj.exists():
            print('MISSING', d); continue
        r = json.loads(rj.read_text())
        out = subprocess.run(['grep', '-a', '-E', r'^Time = |^SIMPLE solution converged|FOAM FATAL|^End|nan', str(log)],
                             capture_output=True, text=True).stdout.splitlines()
        times = [l for l in out if l.startswith('Time = ')]
        last = int(float(times[-1].split('=')[1])) if times else 0
        conv = [l for l in out if l.startswith('SIMPLE solution converged')]
        fatal = any('FOAM FATAL' in l for l in out)
        nconv = int(re.search(r'in (\d+) iterations', conv[0]).group(1)) if conv else None
        n += 1
        ok = (bool(conv) == bool(r['converged'])) and (last == r['iterations']) and (nconv is None or nconv == last) and (fatal == bool(r['fatal']))
        if not ok:
            bad += 1
            print('MISMATCH', g, d.name, 'log:', last, bool(conv), fatal, 'json:', r['iterations'], r['converged'], r['fatal'])
print('checked', n, 'mismatches', bad)
