#!/bin/bash
# Submit cluster_run.sh as a serial skylake-96 job. usage: submit.sh <time> <mem> <sbatch-extra|-> <cluster_run args...>
ROOT=/scratch/cassola/open_jev
T=$1; M=$2; X=$3; shift 3
[ "$X" = "-" ] && X=""
sbatch -p skylake-96 -t "$T" --mem="$M" --ntasks=1 --cpus-per-task=1 $X -J "oj_$2" \
  -o "$ROOT/runs/slurm_$2.out" "$ROOT/scripts/cluster_run.sh" "$@"
