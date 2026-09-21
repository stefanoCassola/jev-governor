#!/bin/bash
# Diagnose laminar BFS convergence: Re 100/400/800 x mesh 20/40 cells per half height, default factors.
B=/scratch/cassola/open_jev/bench; SIF=/work/cassola/openfoam/openfoam2312Mod.sif
FOAM() { apptainer exec --bind /scratch,/work "$SIF" openfoam2312 "$@"; }
mkdir -p $B/diag; cd $B/diag
for n in 20 40; do for re in 100 400 800; do
  c=lbfs_n${n}_Re$re; rm -rf $c; cp -r $B/cases/lbfs_Re800 $c; rm -rf $c/dynamicCode
  sed -i "s/(600 20 1)/($((30*n)) $n 1)/g" $c/system/blockMeshDict
  sed -i "s/^nu .*/nu $(python3 -c "print(1/$re)");/" $c/constant/transportProperties
  sed -i "s/^endTime .*/endTime         6000;/; /^functions/,\$d" $c/system/controlDict
  ( cd $c && FOAM blockMesh > log.blockMesh 2>&1 && FOAM simpleFoam > log.simpleFoam 2>&1; echo "$c $(grep -c '^Time' log.simpleFoam) $(grep -m1 'SIMPLE solution converged' log.simpleFoam)" ) &
done; done; wait
