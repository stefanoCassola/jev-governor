#!/bin/bash
# foamToVTK of selected benchmark runs (copies, originals untouched)
ROOT=/scratch/cassola/open_jev
SIF=/work/cassola/openfoam/openfoam2312Mod.sif
for r in "$@"; do
  n=$(echo $r | tr / _); W=$ROOT/vis/bench/$n; rm -rf $W; mkdir -p $W
  cp -rL $ROOT/bench/runs/$r/. $W/
  apptainer exec --bind /scratch,/work $SIF openfoam2312 foamToVTK -latestTime -case $W > $W/log.foamToVTK 2>&1
  apptainer exec --bind /scratch,/work $SIF openfoam2312 postProcess -func streamFunction -latestTime -case $W > $W/log.sf 2>&1
done
