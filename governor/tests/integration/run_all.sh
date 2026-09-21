#!/bin/bash
# Integration tests: need a sourced OpenFOAM environment, the built library and the jev-governor
# command. No network and no API key: they use the rules and mock backends.
#
#   tests/integration/run_all.sh [work-dir]
set -u
here="$(cd "$(dirname "$0")" && pwd)"
tutorial="$here/../../tutorials/pitzDaily"
work="${1:-$(mktemp -d)}"
mkdir -p "$work"
fails=0

: "${WM_PROJECT_DIR:?source the OpenFOAM environment first}"
command -v jev-governor > /dev/null || { echo "jev-governor is not installed (pip install -e .)"; exit 2; }

check() {   # description, command...
    local what="$1"; shift
    if "$@" > /dev/null 2>&1; then echo "  ok    $what"; else echo "  FAIL  $what"; fails=$((fails + 1)); fi
}

fresh() {   # case name
    rm -rf "${work:?}/$1"; cp -r "$tutorial" "$work/$1"; cd "$work/$1" || exit 2
    blockMesh > log.blockMesh 2>&1
}

iterations() { grep -oE "converged in [0-9]+" log.simpleFoam | grep -oE "[0-9]+"; }

echo "1. sync, rules backend (a fixed factor of 0.7 needs about 790 iterations)"
fresh sync
simpleFoam > log.simpleFoam 2>&1
check "converged" grep -q "SIMPLE solution converged" log.simpleFoam
check "in fewer than 400 iterations ($(iterations))" test "$(iterations)" -lt 400
check "fvSolution restored byte for byte" cmp system/fvSolution "$tutorial/system/fvSolution"
check "fvSchemes restored byte for byte" cmp system/fvSchemes "$tutorial/system/fvSchemes"
check "no back-up left behind" test ! -e system/fvSolution.jevGovernor.orig
check "sidecar exited" grep -q "solver finished" jevGovernor/sidecar.log
check "report works" jev-governor report --case .

echo "2. flip test: forced decisions must reach the solver (mock backend)"
fresh flip
sed -i 's/^endTime .*/endTime 200;/' system/controlDict
JEV_GOVERNOR_BACKEND=mock JEV_GOVERNOR_MOCK='[{"diverging": 1.0}]' simpleFoam > log.simpleFoam 2>&1
check "momentum factor driven to the floor" grep -qE "U +0\.3;" log.simpleFoam
check "solver-side factor changed" grep -q "0.700 -> 0.538" log.simpleFoam
check "upwind fallback switched on at the floor" grep -q "div(phi,U)      bounded Gauss upwind;" log.simpleFoam
check "first-order warning printed" grep -q "first-order upwind" log.simpleFoam
check "fvSchemes restored byte for byte" cmp system/fvSchemes "$tutorial/system/fvSchemes"
# the residual history must differ from the governed run above once the factors differ
a=$(awk '/^Time = 150$/{f=1} f&&/Solving for p/{print $8; exit}' log.simpleFoam)
b=$(awk '/^Time = 150$/{f=1} f&&/Solving for p/{print $8; exit}' "$work/sync/log.simpleFoam")
check "residuals respond to the forced factors ($a vs $b)" test "$a" != "$b"

echo "3. async mode"
fresh async
sed -i 's/mode  *sync;/mode            async;/' system/controlDict
simpleFoam > log.simpleFoam 2>&1
check "converged ($(iterations) iterations)" grep -q "SIMPLE solution converged" log.simpleFoam
check "decisions were applied" grep -q "decision for" log.simpleFoam
check "fvSolution restored byte for byte" cmp system/fvSolution "$tutorial/system/fvSolution"

echo "4. parallel (2 ranks)"
fresh parallel
cat > system/decomposeParDict <<'EOD'
FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }
numberOfSubdomains 2;
method          simple;
coeffs          { n (2 1 1); }
EOD
decomposePar > log.decomposePar 2>&1
mpirun -np 2 simpleFoam -parallel > log.simpleFoam 2>&1
check "converged ($(iterations) iterations)" grep -q "SIMPLE solution converged" log.simpleFoam
check "in fewer than 400 iterations" test "$(iterations)" -lt 400
check "one sidecar only" test "$(grep -c 'starting sidecar' log.simpleFoam)" -eq 1
check "fvSolution restored byte for byte" cmp system/fvSolution "$tutorial/system/fvSolution"

echo "5. recovery after a killed run"
fresh killed
simpleFoam > log.simpleFoam 2>&1 &
pid=$!
while ! grep -q "decision for 50" log.simpleFoam 2> /dev/null; do sleep 0.05; done
kill -9 $pid; wait $pid 2> /dev/null
check "killed run left the back-up" test -e system/fvSolution.jevGovernor.orig
check "killed run left a modified fvSolution" bash -c "! cmp -s system/fvSolution '$tutorial/system/fvSolution'"
sleep 1
check "sidecar noticed the dead solver" bash -c "! ps -eo args | grep -q '[j]ev-governor serve --case .$work/killed'"
simpleFoam > log.simpleFoam 2>&1
check "next run starts from the original file" grep -q "from stale back-up" log.simpleFoam
check "next run starts again from the original factor" grep -q "U 0.700 ->" log.simpleFoam
check "fvSolution restored byte for byte" cmp system/fvSolution "$tutorial/system/fvSolution"

# ---------------------------------------------------------------- transient (PIMPLE) mode
cavity="$here/../../tutorials/cavityPimple"

freshT() {   # case name, number of time steps
    rm -rf "${work:?}/$1"; cp -r "$cavity" "$work/$1"; cd "$work/$1" || exit 2
    sed -i "s/^endTime .*/endTime         $(python3 -c "print($2 * 0.1)");/" system/controlDict
    blockMesh > log.blockMesh 2>&1
}

outer() {   # total outer iterations of a governed run; column 3 of steps.csv
    awk -F, 'NR > 1 { s += $3 } END { print s + 0 }' jevGovernor/steps.csv
}

echo "6. PIMPLE mode, sync, rules backend (the default factors need 4969 outer iterations for 200 steps)"
freshT pimple 200
pimpleFoam > log.pimpleFoam 2>&1
check "run completed" grep -q "^End" log.pimpleFoam
check "one row per time step in steps.csv" test "$(($(wc -l < jevGovernor/steps.csv) - 1))" -eq 200
check "fewer than 4500 outer iterations ($(outer))" test "$(outer)" -lt 4500
check "no unstable step" bash -c "! grep -q unstable jevGovernor/steps.csv"
check "both factors were tried" bash -c "grep -q 'decision for.*: increase.*U ' log.pimpleFoam && grep -q 'decision for.*: increase.*p ' log.pimpleFoam"
check "final factors pFinal/UFinal untouched" bash -c "! grep -E 'decision for' log.pimpleFoam | grep -q Final"
check "fvSolution restored byte for byte" cmp system/fvSolution "$cavity/system/fvSolution"
check "report works" jev-governor report --case .

echo "7. PIMPLE flip test: forced decisions must reach the outer loop (mock backend)"
freshT pimpleFlip 40
JEV_GOVERNOR_BACKEND=mock JEV_GOVERNOR_MOCK='[{"diverging": 1.0}]' pimpleFoam > log.pimpleFoam 2>&1
check "momentum factor lowered" grep -q "decrease_large: U 0.700 -> 0.538" log.pimpleFoam
a=$(awk -F, '$1 == 35 { print $3 }' jevGovernor/steps.csv)
b=$(awk -F, '$1 == 35 { print $3 }' "$work/pimple/jevGovernor/steps.csv")
check "outer iterations respond to the forced factors (step 35: $a vs $b)" test "$a" -gt "$b"

echo "8. PIMPLE mode, async, sidecar started by hand (as on a cluster)"
freshT pimpleAsync 100
sed -i 's/mode  *sync;/mode            async;\n        launchSidecar   no;/' system/controlDict
mkdir -p jevGovernor; echo stale > jevGovernor/done; touch -d "1 hour ago" jevGovernor/done
jev-governor serve --case . --idle-timeout 60 > sidecar.out 2>&1 &
side=$!
pimpleFoam > log.pimpleFoam 2>&1
check "run completed" grep -q "^End" log.pimpleFoam
check "decisions were applied" grep -q "decision for" log.pimpleFoam
for i in $(seq 50); do kill -0 $side 2> /dev/null || break; sleep 0.1; done
check "sidecar exited on its own" bash -c "! kill -0 $side 2> /dev/null"
check "fvSolution restored byte for byte" cmp system/fvSolution "$cavity/system/fvSolution"

echo "9. a solver that dies does not leave the hand-started sidecar hanging"
freshT pimpleKilled 200
sed -i 's/mode  *sync;/mode            sync;\n        launchSidecar   no;/' system/controlDict
jev-governor serve --case . --idle-timeout 60 > sidecar.out 2>&1 &
side=$!
pimpleFoam > log.pimpleFoam 2>&1 &
pid=$!
while ! grep -q "decision for 20" log.pimpleFoam 2> /dev/null; do sleep 0.05; done
kill -9 $pid; wait $pid 2> /dev/null
for i in $(seq 50); do kill -0 $side 2> /dev/null || break; sleep 0.1; done
check "sidecar noticed the dead solver within 5 s" bash -c "! kill -0 $side 2> /dev/null"
kill $side 2> /dev/null

echo "10. jev backend without a usable key: the run goes on ungoverned and says so"
fresh nokey
sed -i 's/^endTime .*/endTime 60;/' system/controlDict
env -u TYPESAFE_API_KEY JEV_GOVERNOR_BACKEND=jev simpleFoam > log.simpleFoam 2>&1
check "run completed" grep -q "^End" log.simpleFoam
check "loud notice in the solver log" grep -q "run is NOT governed" log.simpleFoam
check "the notice says what is missing" grep -qE "TYPESAFE_API_KEY|typesafe-sdk is not installed" log.simpleFoam
check "factors were held" bash -c "! grep -q '^    relaxationFactors' log.simpleFoam"
check "fvSolution restored byte for byte" cmp system/fvSolution "$tutorial/system/fvSolution"

echo
if [ "$fails" -eq 0 ]; then echo "all integration tests passed ($work)"; else echo "$fails FAILED ($work)"; fi
exit "$fails"
