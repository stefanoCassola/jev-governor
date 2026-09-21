#!/bin/bash
# Build benchmark case templates in $B/cases (run inside a job, not on the login node).
# Every template: steady simpleFoam, ascii output, final write only, jevSync function object
# precompiled (dynamicCode is copied along with the template, so runs do not recompile).
set -e
B=/scratch/cassola/open_jev/bench
SIF=/work/cassola/openfoam/openfoam2312Mod.sif
T=$B/tutorials
FOAM() { apptainer exec --bind /scratch,/work "$SIF" openfoam2312 "$@"; }
mkdir -p "$B/cases"; cd "$B/cases"

set_ctrl() {  # <case> <endTime>
  sed -i "s/^endTime .*/endTime         $2;/; s/^writeInterval .*/writeInterval   100000;/; s/^writeFormat .*/writeFormat     ascii;/; s/^writePrecision .*/writePrecision  10;/; s/^runTimeModifiable .*/runTimeModifiable false;/" "$1/system/controlDict"
  grep -q '^writePrecision' "$1/system/controlDict" || echo 'writePrecision  10;' >> "$1/system/controlDict"
  # drop tutorial function objects, add jevSync
  python3 - "$1/system/controlDict" "$B/common/jevSyncFO" <<'PY'
import re, sys
p, fo = sys.argv[1], open(sys.argv[2]).read()
s = open(p).read()
i = s.find("\nfunctions")
if i >= 0:
    # remove the functions block (balanced braces)
    j = s.index("{", i); d = 0
    for k in range(j, len(s)):
        d += s[k] == "{"; d -= s[k] == "}"
        if d == 0: break
    s = s[:i] + s[k + 1:]
s = s.replace("// ************************************************************************* //", "")
s += "\nfunctions\n{\n" + fo + "}\n"
open(p, "w").write(s)
PY
}

compile_fo() {  # run 0 iterations to build dynamicCode
  local c=$1; local e; e=$(sed -n 's/^endTime *\([0-9]*\);/\1/p' $c/system/controlDict)
  sed -i "s/^endTime .*/endTime         1;/" $c/system/controlDict
  (cd $c && FOAM simpleFoam > log.compile 2>&1) || { tail -30 $c/log.compile; exit 1; }
  grep -q "jevSync: interval" $c/log.compile || { echo "FO not active in $c"; tail -30 $c/log.compile; exit 1; }
  sed -i "s/^endTime .*/endTime         $e;/" $c/system/controlDict
  rm -rf $c/1 $c/0/uniform $c/log.compile
}

# ---------------------------------------------------------------- laminar lid-driven cavity (Ghia 1982)
make_cavity() {  # <Re>
  local c=cavity_Re$1; rm -rf $c; cp -r "$T/pitzDaily" $c; rm -rf $c/0/* $c/system/streamlines
  cat > $c/system/blockMeshDict <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }
scale 1;
vertices ((0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 0.01) (1 0 0.01) (1 1 0.01) (0 1 0.01));
blocks (hex (0 1 2 3 4 5 6 7) (128 128 1) simpleGrading (1 1 1));
boundary
(
    movingWall { type wall; faces ((3 7 6 2)); }
    fixedWalls { type wall; faces ((0 4 7 3) (2 6 5 1) (1 5 4 0)); }
    frontAndBack { type empty; faces ((0 3 2 1) (4 5 6 7)); }
);
EOF
  cat > $c/constant/transportProperties <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object transportProperties; }
transportModel Newtonian;
nu $(python3 -c "print(1/$1)");
EOF
  cat > $c/constant/turbulenceProperties <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object turbulenceProperties; }
simulationType laminar;
EOF
  cat > $c/0/U <<EOF
FoamFile { version 2.0; format ascii; class volVectorField; object U; }
dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField
{
    movingWall { type fixedValue; value uniform (1 0 0); }
    fixedWalls { type noSlip; }
    frontAndBack { type empty; }
}
EOF
  cat > $c/0/p <<EOF
FoamFile { version 2.0; format ascii; class volScalarField; object p; }
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    movingWall { type zeroGradient; }
    fixedWalls { type zeroGradient; }
    frontAndBack { type empty; }
}
EOF
  cat > $c/system/fvSchemes <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
EOF
  cat > $c/system/fvSolution <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }
solvers
{
    p { solver GAMG; smoother GaussSeidel; tolerance 1e-8; relTol 0.05; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.1; }
}
SIMPLE
{
    nNonOrthogonalCorrectors 0;
    consistent      no;
    pRefCell        0;
    pRefValue       0;
    residualControl { p 1e-5; U 1e-6; }
}
relaxationFactors
{
    fields { p 0.3; }
    equations { U 0.7; }
}
EOF
  set_ctrl $c 20000
  (cd $c && FOAM blockMesh > log.blockMesh 2>&1)
  compile_fo $c
}

# ---------------------------------------------------------------- laminar backward-facing step (Gartling 1990, ER 2)
make_lbfs() {  # <Re>
  local c=lbfs_Re$1; rm -rf $c; cp -r cavity_Re100 $c; rm -rf $c/constant/polyMesh $c/dynamicCode
  cat > $c/system/blockMeshDict <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }
scale 1;
// channel height H = 1 (y in [-0.5, 0.5]), step height 0.5, length 30; inflow on the upper half at x = 0
vertices ((0 -0.5 0) (30 -0.5 0) (30 0 0) (0 0 0) (30 0.5 0) (0 0.5 0)
          (0 -0.5 0.05) (30 -0.5 0.05) (30 0 0.05) (0 0 0.05) (30 0.5 0.05) (0 0.5 0.05));
blocks
(
    hex (0 1 2 3 6 7 8 9) (1200 40 1) simpleGrading (1 1 1)
    hex (3 2 4 5 9 8 10 11) (1200 40 1) simpleGrading (1 1 1)
);
boundary
(
    inlet { type patch; faces ((3 9 11 5)); }
    outlet { type patch; faces ((1 2 8 7) (2 4 10 8)); }
    walls { type wall; faces ((0 6 9 3) (0 1 7 6) (5 11 10 4)); }
    frontAndBack { type empty; faces ((0 3 2 1) (3 5 4 2) (6 7 8 9) (9 8 10 11)); }
);
EOF
  sed -i "s/^nu .*/nu $(python3 -c "print(1/$1)");/" $c/constant/transportProperties
  cat > $c/0/U <<'EOF'
FoamFile { version 2.0; format ascii; class volVectorField; object U; }
dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField
{
    inlet
    {
        type        exprFixedValue;
        value       uniform (0 0 0);
        valueExpr   "vector(24*pos().y()*(0.5 - pos().y()), 0, 0)";
    }
    outlet { type zeroGradient; }
    walls { type noSlip; }
    frontAndBack { type empty; }
}
EOF
  cat > $c/0/p <<'EOF'
FoamFile { version 2.0; format ascii; class volScalarField; object p; }
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    inlet { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    walls { type zeroGradient; }
    frontAndBack { type empty; }
}
EOF
  sed -i '/pRefCell/d; /pRefValue/d' $c/system/fvSolution
  (cd $c && FOAM blockMesh > log.blockMesh 2>&1)
  compile_fo $c
}

# ---------------------------------------------------------------- turbulent tutorials
make_tut() {  # <tutorial> <name> <endTime>
  local c=$2; rm -rf $c; cp -r "$T/$1" $c
  [ -d $c/0.orig ] && { rm -rf $c/0; cp -r $c/0.orig $c/0; }
  rm -rf $c/Allrun* $c/Allclean $c/plot $c/README.md $c/system/{streamlines,sample,sampleCp,pressureCoefficient,stressComponents}
  set_ctrl $c $3
  [ -d $c/constant/polyMesh.orig ] && cp -r $c/constant/polyMesh.orig $c/constant/polyMesh
  [ -f $c/system/blockMeshDict ] && (cd $c && FOAM blockMesh > log.blockMesh 2>&1)
  compile_fo $c
}

if [ "${1:-all}" = lbfs ]; then
  make_lbfs 400; make_lbfs 800
else
  for re in 100 400 1000; do make_cavity $re; done
  make_lbfs 400; make_lbfs 800
  make_tut pitzDaily pitzDaily 20000
fi
for c in */; do echo "$c $(grep -m1 note ${c}constant/polyMesh/owner | grep -o 'nCells:[0-9]*') $(ls ${c}dynamicCode/platforms/*/lib 2>/dev/null | head -1)"; done
