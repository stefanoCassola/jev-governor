#!/bin/bash
# Fixed under-relaxation factors versus the governor, from the same starting factors.
#
#   benchmarks/run_benchmarks.sh [work-dir] [backend]      backend: rules (default) | jev
#
# Cases: two OpenFOAM tutorials. pitzDaily converges; airFoil2D plateaus above its residualControl
# targets whatever the factors are, so there the residual level at the end is what counts.
set -u
: "${WM_PROJECT_DIR:?source the OpenFOAM environment first}"
here="$(cd "$(dirname "$0")" && pwd)"
work="${1:-$(mktemp -d)}"
backend="${2:-rules}"
jobs="${JOBS:-4}"
mkdir -p "$work"; cd "$work" || exit 2
tut="$FOAM_TUTORIALS/incompressible/simpleFoam"

governor() {  # fields followers
cat <<EOG
functions
{
    jevGovernor
    {
        type jevGovernor; libs (jevGovernor);
        interval 25; backend $backend;
        fields ($1); followers ($2);
    }
}
EOG
}

prepare() {  # dir case factor pFactor maxIter governed fields followers
    rm -rf "$1"; cp -r "$tut/$2" "$1"
    (
        cd "$1" || exit
        [ -d 0.orig ] && cp -r 0.orig 0
        [ -d constant/polyMesh.orig ] && cp -r constant/polyMesh.orig constant/polyMesh
        [ -f system/blockMeshDict ] && blockMesh > log.blockMesh 2>&1
        python3 "$here/set_case.py" . "$3" "$4" "$5"
        sed -i '/^functions/,/^}/d' system/controlDict      # no tutorial post-processing in either run
        [ "$6" = yes ] && governor "$7" "$8" >> system/controlDict
    )
}

n=0
launch() {  # dir
    ( cd "$1" && /usr/bin/time -f "%e" -o wall.txt simpleFoam > log.simpleFoam 2>&1 ) &
    n=$((n + 1)); [ $((n % jobs)) -eq 0 ] && wait
}

for f in 0.5 0.7 0.9 0.95 0.99; do
    prepare pitzDaily_fixed_$f pitzDaily $f - 1500 no;  launch pitzDaily_fixed_$f
    prepare pitzDaily_gov_$f   pitzDaily $f - 1500 yes "U p k epsilon" "k epsilon"; launch pitzDaily_gov_$f
done
for f in 0.5 0.7 0.9; do
    p=$(python3 -c "print(round(1-$f,2))")
    prepare airFoil2D_fixed_$f airFoil2D $f $p 2000 no;  launch airFoil2D_fixed_$f
    prepare airFoil2D_gov_$f   airFoil2D $f $p 2000 yes "U p nuTilda" "nuTilda"; launch airFoil2D_gov_$f
done
wait

python3 "$here/summarise.py" "$work" "$backend"
