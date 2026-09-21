#!/bin/bash
# FiberGeo study: one exclusive node per case; default, fixed 0.95/1.0 and Jev run side by side.
# usage (sbatch): fg_node.sh <case_name>
ROOT=/scratch/cassola/open_jev
C=$1
"$ROOT/scripts/cluster_run.sh" "$C" "fg_${C}_default" &
"$ROOT/scripts/cluster_run.sh" "$C" "fg_${C}_s_U95_p10" --relax 0.95 1.0 &
"$ROOT/scripts/cluster_run.sh" "$C" "fg_${C}_jev" --jev --interval 10 --start 10 &
wait
