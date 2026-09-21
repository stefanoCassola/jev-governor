#!/bin/bash
# usage: fg_extract.sh <case> <run> [<run> ...]   (runs relative to $ROOT/runs)
set -e
ROOT=/scratch/cassola/open_jev
SIF=/work/cassola/openfoam/openfoam2312Mod.sif
CASE=$1; shift
W=$ROOT/vis/$CASE; mkdir -p $W/cc/system $W/cc/1
ln -sfn $ROOT/cases/$CASE/constant $W/cc/constant
cp $ROOT/runs/$1/system/fvSchemes $ROOT/runs/$1/system/fvSolution $W/cc/system/
cat > $W/cc/system/controlDict <<EOC
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application simpleFoam; startFrom latestTime; startTime 0; stopAt endTime; endTime 1; deltaT 1;
writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 10; timeFormat general; timePrecision 6;
EOC
if [ ! -s $W/cc/1/C ]; then
  apptainer exec --bind /scratch,/work $SIF openfoam2312 postProcess -func writeCellCentres -case $W/cc > $W/log.cc 2>&1
  rm -f $W/cc/1/Cx $W/cc/1/Cy $W/cc/1/Cz
fi
RUNS=(); for r in "$@"; do RUNS+=($ROOT/runs/$r); done
$ROOT/.venv/bin/python $ROOT/vis/fg_voxelize.py $W/cc/1/C $W "${RUNS[@]}"
