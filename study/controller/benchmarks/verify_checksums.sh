#!/bin/bash
# Verify the frozen controller files against the checksums recorded when they were frozen.
# (The three SHA256SUMS files were written with different relative paths; this compares by file name.)
cd "$(dirname "$0")"; rc=0
for d in frozen_v3 frozen_v4 frozen_v4a; do
  while read -r sum path; do
    f="$d/$(basename "$path")"
    if [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$sum" ]; then echo "OK    $f"; else echo "FAIL  $f"; rc=1; fi
  done < "$d/SHA256SUMS"
done
exit $rc
