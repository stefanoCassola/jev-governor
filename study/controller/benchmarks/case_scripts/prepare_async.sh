#!/bin/bash
# Copies of the benchmark templates with the async-capable jevSync function object (precompiled).
set -e
B=/scratch/cassola/open_jev/bench; SIF=/work/cassola/openfoam/openfoam2312Mod.sif
FOAM() { apptainer exec --bind /scratch,/work "$SIF" openfoam2312 "$@"; }
mkdir -p $B/cases_async; cd $B/cases_async
for c in $(ls $B/cases); do
  rm -rf $c; cp -r $B/cases/$c $c; rm -rf $c/dynamicCode
  python3 - $c/system/controlDict "$B/common/jevSyncFO_async" <<'PY'
import sys
p, fo = sys.argv[1], open(sys.argv[2]).read()
s = open(p).read(); i = s.index("\nfunctions")
s = s[:i] + "\nfunctions\n{\n" + fo + "}\n"
open(p, "w").write(s)
PY
  e=$(sed -n 's/^endTime *\([0-9]*\);/\1/p' $c/system/controlDict)
  sed -i "s/^endTime .*/endTime         1;/" $c/system/controlDict
  (cd $c && FOAM simpleFoam > log.compile 2>&1) || { tail -20 $c/log.compile; exit 1; }
  grep -q "jevSync: interval" $c/log.compile || { echo "FO inactive $c"; exit 1; }
  sed -i "s/^endTime .*/endTime         $e;/" $c/system/controlDict
  rm -rf $c/1 $c/0/uniform $c/log.compile
  echo "$c ok"
done
