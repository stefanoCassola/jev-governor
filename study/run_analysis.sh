#!/bin/bash
# Regenerate every table, number and figure in results/ from data/.
# usage: ./run_analysis.sh [python]     (needs numpy pandas matplotlib; pyvista for the flow-field figure)
set -e
PY=${1:-python3}; case "$PY" in */*) PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")";; esac
cd "$(dirname "$0")/analysis"
R=../results
mkdir -p $R/tables $R/figures
mkdir -p ../data/_records && tar -xzf ../data/archives/benchmark_decision_records.tar.gz -C ../data/_records
$PY make_tables.py ../data $R
$PY make_perm.py ../data $R
$PY make_accuracy.py ../data $R
$PY stale_decisions.py ../data/_records $R
$PY make_transient.py ../data $R
mkdir -p ../data/_records_t && tar -xzf ../data/archives/transient_run_records.tar.gz -C ../data/_records_t
$PY make_repeatability.py ../data $R
$PY fig_transient.py ../data/histories/transient_selected.json $R/figures/fig_transient
$PY fig_landscape.py ../data $R/figures/fig_landscape
$PY fig_iters.py ../data $R/figures/fig_iters
$PY fig_anatomy.py ../data/histories/bench_selected.json $R/figures/fig_anatomy
mkdir -p ../data/_fields && tar -xzf ../data/archives/benchmark_fields_vtk.tar.gz -C ../data/_fields
$PY fig_flow2d.py ../data/_fields/bench $R/figures
if [ -d ../data/fields_3d ]; then   # 1.2 GB of voxelised 3D fields, not in the repository (see README)
  $PY fig_fibre3d.py ../data/fields_3d $R/figures/fig_fibre3d
  $PY fig_perm_anatomy.py ../data/histories/perm_p90_x.json ../data/fields_3d $R/figures/fig_perm_anatomy
fi
