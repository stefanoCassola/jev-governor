#!/bin/bash
# Jev reruns with the patched solver (runtime-readable permeability stop switch).
# Three serial runs per exclusive node, the same load level as the FiberGeo study (fg_node.sh).
# usage (sbatch --exclusive): fg_jevrr_node.sh <case> [<case> ...]   -> runs/fg_<case>_jevrr
ROOT=/scratch/cassola/open_jev
for C in "$@"; do
  "$ROOT/scripts/fg_jev_rerun.sh" "$C" "fg_${C}_jevrr" &
done
wait
