#!/bin/bash
# Run one serial simpleFoamMod case on the cluster, optionally with the Jev relaxation controller.
# Submitted via sbatch (see submit.sh). usage:
#   cluster_run.sh <case: base|dev> <run_name> [--relax U p] [--no-stop ENDTIME] [--jev [controller args...]]
set -u
ROOT=/scratch/cassola/open_jev
SIF=/work/cassola/openfoam/openfoam2312Mod.sif
CASE=$1; NAME=$2; shift 2
RUN=$ROOT/runs/$NAME
[ -e "$RUN" ] && { echo "exists: $RUN"; exit 1; }
mkdir -p "$RUN"
cp -r "$ROOT/cases/$CASE/system" "$ROOT/cases/$CASE/0" "$RUN/"
ln -s "$ROOT/cases/$CASE/constant" "$RUN/constant"   # mesh is read-only for simpleFoamMod

JEV=0; JEV_ARGS=()
while [ $# -gt 0 ]; do
  case $1 in
    --relax) sed -i "s/^\(\s*U\s\+\)[0-9.]\+;/\1$2;/; s/^\(\s*p\s\+\)[0-9.]\+;/\1$3;/" "$RUN/system/fvSolution"; shift 3;;
    --no-stop) sed -i 's/convPermeability\s\+true;/convPermeability        false;/' "$RUN/system/fvSolution"
               sed -i "s/^endTime .*/endTime         $2;/" "$RUN/system/controlDict"; shift 2;;
    --jev) JEV=1; shift; JEV_ARGS=("$@"); break;;
    *) echo "bad arg $1"; exit 1;;
  esac
done

cd "$RUN"
{ echo "host $(hostname) job ${SLURM_JOB_ID:-none}"; lscpu | grep 'Model name'; grep -E '^\s+(U|p)\s' system/fvSolution; } > run_info
date +%s.%N > start_epoch
stdbuf -oL -eL apptainer exec --bind /scratch,/work "$SIF" openfoam2312 simpleFoamMod -case "$RUN" > log.simpleFoamMod 2>&1 &
SOLVER=$!
if [ $JEV = 1 ]; then
  "$ROOT/.venv/bin/python" "$ROOT/python/jev_relax.py" "$RUN" "${JEV_ARGS[@]}" > log.jev 2>&1 &
  CTRL=$!
fi
wait $SOLVER; echo "solver exit $?" >> run_info
date +%s.%N > end_epoch
touch supervisor_done   # tells the controller the solver is gone
[ $JEV = 1 ] && wait $CTRL
exit 0
