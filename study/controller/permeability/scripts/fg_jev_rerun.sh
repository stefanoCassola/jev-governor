#!/bin/bash
# Rerun only the Jev run of a permeability case with the patched solver (runtime-readable stop switch).
# usage (sbatch): fg_jev_rerun.sh <case_name> <run_name>
ROOT=/scratch/cassola/open_jev
SOLVER_BIN=$ROOT/solver/bin/simpleFoamMod "$ROOT/scripts/cluster_run2.sh" "$1" "$2" --jev --interval 10 --start 10
