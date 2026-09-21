#!/bin/bash
# Create a serial dev run directory from cases/dev. usage: make_dev_run.sh <name>
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN=$ROOT/runs/$1
[ -e "$RUN" ] && { echo "exists: $RUN"; exit 1; }
mkdir -p "$RUN"; cp -r "$ROOT/cases/dev/system" "$ROOT/cases/dev/0" "$RUN/"; cp -al "$ROOT/cases/dev/constant" "$RUN/"
"$ROOT/scripts/set_checkpoint.sh" "$RUN" 10
echo "$RUN"
