#!/bin/bash
# Pawar & Maulik (2021) replication templates from their public base case (PAR-RL repo):
# Driver & Seegmiller backward-facing step, k-omega SST, SIMPLEC, 20540 cells.
# Test criterion of Pawar & Maulik (2021): U, k, omega residuals < 5e-4, p < 5e-2. Inlet k, omega from their
# formula k = 1.5 (U I)^2, omega = k / (nu' * 0.009) with I = 6.1e-4 and nu' = 6.7e-7 (as in their code).
set -e
B=/scratch/cassola/open_jev/bench
SIF=/work/cassola/openfoam/openfoam2312Mod.sif
SRC=$B/PAR-RL/examples/OF_turbulence_model/baseCase
FOAM() { apptainer exec --bind /scratch,/work "$SIF" openfoam2312 "$@"; }
cd "$B/cases"
for V in 25 44.2 50 75; do
  c=pm_bfs_U${V%.*}; rm -rf $c; cp -r "$SRC" $c
  rm -rf $c/system/{sample,sampleCp,pressureCoefficient,stressComponents} $c/cf_expt.csv $c/logr.remove
  read K W < <(python3 -c "k=1.5*($V*6.1e-4)**2; print(f'{k:.10g}', f'{k/(6.7e-7*0.009):.10g}')")
  sed -i "s/^internalField .*/internalField uniform $K;/" $c/0/k
  sed -i "s/^internalField .*/internalField uniform $W;/" $c/0/omega
  sed -i "s/value uniform (44.2 0 0);/value uniform ($V 0 0);/" $c/0/U
  python3 - $c/system/fvSolution <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
s = re.sub(r"residualControl\s*\{[^}]*\}",
           'residualControl\n    {\n        p               5e-2;\n        U               5e-4;\n'
           '        "(k|omega)"     5e-4;\n    }', s)
open(p, "w").write(s)
PY
  # controlDict: start from 0, final write only, jevSync instead of their squareU object
  sed -i "s/^startFrom .*/startFrom       startTime;/" $c/system/controlDict
  python3 - $c/system/controlDict "$B/common/jevSyncFO" <<'PY'
import sys
p, fo = sys.argv[1], open(sys.argv[2]).read()
s = open(p).read()
i = s.find("\nfunctions")
if i >= 0:
    j = s.index("{", i); d = 0
    for k in range(j, len(s)):
        d += s[k] == "{"; d -= s[k] == "}"
        if d == 0: break
    s = s[:i] + s[k + 1:]
s += "\nfunctions\n{\n" + fo + "}\n"
open(p, "w").write(s)
PY
  sed -i "s/^endTime .*/endTime         1;/; s/^writeInterval .*/writeInterval   100000;/; s/^writePrecision .*/writePrecision  10;/; s/^runTimeModifiable .*/runTimeModifiable false;/" $c/system/controlDict
  (cd $c && FOAM simpleFoam > log.compile 2>&1) || { tail -30 $c/log.compile; exit 1; }
  grep -q "jevSync: interval" $c/log.compile || { echo "FO not active in $c"; tail -30 $c/log.compile; exit 1; }
  sed -i "s/^endTime .*/endTime         4000;/" $c/system/controlDict
  rm -rf $c/1 $c/0/uniform $c/log.compile
  echo "$c k=$K omega=$W $(ls $c/dynamicCode/platforms/*/lib)"
done
