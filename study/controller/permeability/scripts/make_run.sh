#!/bin/bash
# Create a serial run directory from the base case (mesh hard-linked, 0/ and system/ copied).
# usage: make_run.sh <run_name>
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BASE=$ROOT/cases/base
RUN=$ROOT/runs/$1
[ -e "$RUN" ] && { echo "exists: $RUN"; exit 1; }
mkdir -p "$RUN"
cp -r "$BASE/system" "$RUN/system"
cp -al "$BASE/constant" "$RUN/constant"
cp -r "$BASE/0" "$RUN/0"
touch "$RUN/foam.foam"
"$ROOT/scripts/set_checkpoint.sh" "$RUN" 20
echo "$RUN"
