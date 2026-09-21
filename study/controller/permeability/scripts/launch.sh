#!/bin/bash
# Launch a supervised (auto-restarting) serial simpleFoamMod run pinned to one CPU. usage: launch.sh <rundir> <cpu>
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$1" || exit 1
setsid nohup bash -c "date +%s.%N > start_epoch; $ROOT/.venv/bin/python $ROOT/python/supervise.py . $2 > log.supervisor 2>&1; date +%s.%N > end_epoch" >/dev/null 2>&1 &
