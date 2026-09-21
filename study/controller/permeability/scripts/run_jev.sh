#!/bin/bash
# Launch simpleFoamMod pinned to a CPU plus the Jev relaxation controller.
# usage: run_jev.sh <rundir> <cpu> [controller args...]
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN=$(cd "$1" && pwd); CPU=$2; shift 2
"$ROOT/scripts/launch.sh" "$RUN" "$CPU"
cd "$RUN"
setsid nohup "$ROOT/.venv/bin/python" "$ROOT/python/jev_relax.py" "$RUN" "$@" > "$RUN/log.jev" 2>&1 &
